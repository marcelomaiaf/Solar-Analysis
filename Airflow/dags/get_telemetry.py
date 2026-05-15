import json
import os
import time
from datetime import datetime, timedelta, timezone
from html import escape
from zoneinfo import ZoneInfo
from openai import OpenAI
import pandas as pd
import pvlib
import requests
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.sdk import dag, get_current_context, task
from airflow.utils.email import send_email_smtp
from cryptography.fernet import Fernet

tz = ZoneInfo("America/Sao_Paulo")

key = os.getenv("CREDENTIALS_ENCRYPTION_KEY")
weg_url = "https://solarportal-api.weg.net/api/v1/measurements"
open_meteo_url = "https://api.open-meteo.com/v1/forecast"
default_tilt_deg = 10
default_azimuth_deg = 0
default_loss_percent = 14
default_gamma_pdc = -0.004
energy_price_brl_per_kwh = 0.725
report_sender_email = "marcelomaiaffilho@gmail.com"
report_recipient_email = "marcelomaiaffilho@gmail.com"
expected_generation_tolerance = 0.10
hourly_variables = [
        "shortwave_radiation",
        "direct_radiation",
        "diffuse_radiation",
        "direct_normal_irradiance",
        "temperature_2m",
        "relative_humidity_2m",
        "dewpoint_2m",
        "precipitation",
        "rain",
        "cloud_cover",
        "cloud_cover_low",
        "cloud_cover_mid",
        "cloud_cover_high",
        "wind_speed_10m",
        "wind_gusts_10m",
        "surface_pressure",
        "weather_code",
        "is_day",
        "sunshine_duration",
    ]

daily_variables = [
    "sunrise",
    "sunset",
    "daylight_duration",
    "sunshine_duration",
    "shortwave_radiation_sum",
    "precipitation_sum",
    "rain_sum",
    "precipitation_hours",
    "temperature_2m_max",
    "temperature_2m_min",
    "wind_speed_10m_max",
]
sql_query = """
            select
                p.name,
                v.vendor,
                p.timezone,
                p.area_m2,
                p.module_efficiency,
                p.location,
                p.address,
                p.latitude,
                p.longitude,
                p.azimuth_deg,
                p.tilt_deg,
                v.plant_id,
                v.vendor_account_id,
                v.vendor_plant_id,
                v.vendor_device_id,
                v.vendor_timezone,
                a.auth_type,
                a.credentials_encrypted
            from plants p
            join vendor_plant_links v
                on p.id = v.plant_id
            join vendor_accounts a
                on p.tenant_id = a.tenant_id
            and a.vendor = v.vendor
            where v.vendor = 'weg';
            """ # reduzir o número de campos puxados para o stritamente necessário
def decrypt(encrypted_text,key):
    key = Fernet(key)
    decrypt = key.decrypt(encrypted_text)
    return json.loads(decrypt)

def row_as_dict(cursor):
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns,row)) for row in cursor.fetchall()]

def get_target_date(context=None):
    if context is None:
        context = get_current_context()
    logical_date = context["logical_date"]
    if logical_date is None:
        raise ValueError("logical_date ausente no contexto do Airflow")
    return logical_date.date() - timedelta(days=1)

def utc_day_window(day):
    date_from = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=tz)
    date_to = datetime(day.year, day.month, day.day, 23, 59, 0, tzinfo=tz)
    date_from_str = date_from.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    date_to_str = date_to.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return date_from_str, date_to_str

def target_date_from_weather_result(weather_result):
    target_date = weather_result.get("target_date")
    if target_date:
        return datetime.fromisoformat(target_date).date()
    return get_target_date()

def float_or_default(value, default):
    if value is None or value == "":
        return float(default)
    return float(value)

def required_float(value, field_name):
    if value is None or value == "":
        raise ValueError(f"Campo obrigatorio ausente para estimativa: {field_name}")
    return float(value)

def required_positive_float(value, field_name):
    number = required_float(value, field_name)
    if number <= 0:
        raise ValueError(f"Campo precisa ser positivo para estimativa: {field_name}")
    return number



def normalize_module_efficiency(value):
    efficiency = required_positive_float(value, "module_efficiency")
    if efficiency > 1:
        efficiency = efficiency / 100
    if efficiency <= 0 or efficiency > 1:
        raise ValueError("module_efficiency deve ser uma fracao entre 0 e 1 ou percentual entre 0 e 100")
    return efficiency

def hourly_series(hourly_data, key, index, default):
    values = hourly_data.get(key)
    if values is None:
        return pd.Series(default, index=index, dtype="float64")
    if len(values) != len(index):
        raise ValueError(f"Open-Meteo retornou {key} com tamanho diferente de time")
    return pd.to_numeric(pd.Series(values, index=index), errors="coerce").fillna(default)

def estimate_generation_kwh_from_open_meteo(weather_result, target_date):
    forecast = weather_result.get("open_meteo_forecast") or {}
    hourly_data = forecast.get("hourly") or {}
    hourly_times = hourly_data.get("time") or []
    if not hourly_times:
        raise ValueError("Open-Meteo nao retornou dados horarios para estimar geracao")

    timezone_name = weather_result.get("timezone") or forecast.get("timezone") or "America/Sao_Paulo"
    index = pd.to_datetime(hourly_times)
    if index.tz is None:
        index = index.tz_localize(timezone_name)
    else:
        index = index.tz_convert(timezone_name)

    weather = pd.DataFrame({
        "ghi": hourly_series(hourly_data, "shortwave_radiation", index, 0),
        "dni": hourly_series(hourly_data, "direct_normal_irradiance", index, 0),
        "dhi": hourly_series(hourly_data, "diffuse_radiation", index, 0),
        "temp_air": hourly_series(hourly_data, "temperature_2m", index, 25),
        "wind_speed_ms": hourly_series(hourly_data, "wind_speed_10m", index, 3.6) / 3.6,
    })
    daily_weather = weather[weather.index.date == target_date]
    if daily_weather.empty:
        raise ValueError(f"Open-Meteo nao retornou dados horarios para {target_date.isoformat()}")

    latitude = required_float(weather_result.get("latitude"), "latitude")
    longitude = required_float(weather_result.get("longitude"), "longitude")
    tilt_deg = float_or_default(weather_result.get("tilt_deg"), default_tilt_deg)
    azimuth_deg = float_or_default(weather_result.get("azimuth_deg"), default_azimuth_deg)
    area_m2 = required_positive_float(weather_result.get("area_m2"), "area_m2")
    module_efficiency = normalize_module_efficiency(weather_result.get("module_efficiency"))
    loss_percent = float_or_default(weather_result.get("loss_percent"), default_loss_percent)

    location = pvlib.location.Location(latitude=latitude, longitude=longitude, tz=timezone_name)
    solar_position = location.get_solarposition(daily_weather.index)
    poa = pvlib.irradiance.get_total_irradiance(
        surface_tilt=tilt_deg,
        surface_azimuth=azimuth_deg,
        solar_zenith=solar_position["apparent_zenith"],
        solar_azimuth=solar_position["azimuth"],
        dni=daily_weather["dni"],
        ghi=daily_weather["ghi"],
        dhi=daily_weather["dhi"],
    )
    cell_temperature = pvlib.temperature.pvsyst_cell(
        poa_global=poa["poa_global"],
        temp_air=daily_weather["temp_air"],
        wind_speed=daily_weather["wind_speed_ms"],
    )
    temperature_factor = 1 + default_gamma_pdc * (cell_temperature - 25)
    dc_power_w = poa["poa_global"] * area_m2 * module_efficiency * temperature_factor
    loss_factor = max(0, min(loss_percent, 100)) / 100
    estimated_power_w = pd.Series(dc_power_w, index=daily_weather.index).clip(lower=0) * (1 - loss_factor)
    estimated_generation_kwh = round(float(estimated_power_w.sum() / 1000), 3)

    return {
        "plant_id": weather_result.get("plant_id"),
        "vendor_plant_id": weather_result.get("vendor_plant_id"),
        "plant_name": weather_result.get("plant_name"),
        "data": {
            "date": target_date.isoformat(),
            "plant_id": weather_result.get("plant_id"),
            "vendor_plant_id": weather_result.get("vendor_plant_id"),
            "estimated_generation_kwh": estimated_generation_kwh,
            "expected_generation_kwh": estimated_generation_kwh,
            "peak_power_kw": round(float(estimated_power_w.max() / 1000), 3),
            "hours": int(len(daily_weather)),
            "area_m2": round(float(area_m2), 3),
            "module_efficiency": round(float(module_efficiency), 4),
            "source": "Open-Meteo irradiance + pvlib area-based model",
        }
    }

def telemetry_points(telemetry_result):
    telemetry = telemetry_result.get("telemetry") or {}
    data = telemetry.get("data") if isinstance(telemetry, dict) else None
    return data if isinstance(data, list) else []

def measured_generation_kwh(telemetry_result):
    values = []
    for point in telemetry_points(telemetry_result):
        value = point.get("value") if isinstance(point, dict) else None
        if value is not None:
            values.append(max(float(value), 0))
    return round(sum(values) * 0.25, 3)

def expected_data_by_vendor_plant(expected_generation):
    items = {}
    for expected in expected_generation:
        data = expected.get("data") or {}
        vendor_plant_id = expected.get("vendor_plant_id") or data.get("vendor_plant_id")
        if vendor_plant_id is not None:
            items[str(vendor_plant_id)] = expected
    return items

def simple_report(analysis_results):
    lines = ["Relatorio diario de geracao solar", ""]
    for item in analysis_results:
        status = "dentro do esperado" if item["within_expected_range"] else "fora do esperado"
        lines.append(
            f"{item['plant_name']}: gerou {item['measured_generation_kwh']} kWh, "
            f"esperado {item['expected_generation_kwh']} kWh, {status}. "
            f"Perda estimada: {item['loss_kwh']} kWh / R$ {item['loss_brl']}."
        )
    return "\n".join(lines)

def format_kwh(value):
    return f"{float(value):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")

def format_brl(value):
    return f"R$ {float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

def build_generation_email_html(analysis_results):
    target_date = next((item.get("target_date") for item in analysis_results if item.get("target_date")), None)
    plants_outside_range = [item for item in analysis_results if not item.get("within_expected_range")]
    total_loss_kwh = round(sum(float(item.get("loss_kwh") or 0) for item in analysis_results), 3)
    total_loss_brl = round(sum(float(item.get("loss_brl") or 0) for item in analysis_results), 2)

    if plants_outside_range:
        summary = (
            f"{len(plants_outside_range)} usina(s) ficaram abaixo da faixa esperada. "
            f"Perda estimada total: {format_kwh(total_loss_kwh)} kWh ({format_brl(total_loss_brl)})."
        )
    else:
        summary = "Todas as usinas ficaram dentro da faixa esperada de geracao."

    rows = []
    for item in analysis_results:
        plant_name = escape(str(item.get("plant_name") or "Usina sem nome"))
        status_label = "Dentro do esperado" if item.get("within_expected_range") else "Abaixo do esperado"
        status_color = "#166534" if item.get("within_expected_range") else "#b91c1c"
        measured = format_kwh(item.get("measured_generation_kwh") or 0)
        expected = format_kwh(item.get("expected_generation_kwh") or 0)
        expected_min = format_kwh(item.get("expected_min_kwh") or 0)
        expected_max = format_kwh(item.get("expected_max_kwh") or 0)
        loss_kwh = format_kwh(item.get("loss_kwh") or 0)
        loss_brl = format_brl(item.get("loss_brl") or 0)
        rows.append(
            "<tr>"
            f"<td style=\"padding:10px 8px;border-bottom:1px solid #e5e7eb;font-weight:600;color:#111827;\">{plant_name}</td>"
            f"<td style=\"padding:10px 8px;border-bottom:1px solid #e5e7eb;color:#111827;\">{measured} kWh</td>"
            f"<td style=\"padding:10px 8px;border-bottom:1px solid #e5e7eb;color:#111827;\">{expected} kWh</td>"
            f"<td style=\"padding:10px 8px;border-bottom:1px solid #e5e7eb;color:#374151;\">{expected_min} a {expected_max} kWh</td>"
            f"<td style=\"padding:10px 8px;border-bottom:1px solid #e5e7eb;color:{status_color};font-weight:600;\">{status_label}</td>"
            f"<td style=\"padding:10px 8px;border-bottom:1px solid #e5e7eb;color:#111827;\">{loss_kwh} kWh / {loss_brl}</td>"
            "</tr>"
        )

    period_text = f" referente a {escape(target_date)}" if target_date else ""
    rows_html = "\n".join(rows)
    return f"""<!doctype html>
<html>
  <body style="margin:0;padding:0;background:#f8fafc;font-family:Arial,Helvetica,sans-serif;color:#111827;">
    <div style="max-width:760px;margin:0 auto;padding:24px 16px;">
      <h1 style="margin:0 0 8px;font-size:22px;line-height:1.3;color:#0f172a;">Relatorio diario de geracao solar</h1>
      <p style="margin:0 0 18px;font-size:14px;line-height:1.5;color:#475569;">Resumo{period_text}: {escape(summary)}</p>
      <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="border-collapse:collapse;background:#ffffff;border:1px solid #e5e7eb;">
        <thead>
          <tr style="background:#f1f5f9;">
            <th align="left" style="padding:10px 8px;font-size:12px;color:#475569;">Usina</th>
            <th align="left" style="padding:10px 8px;font-size:12px;color:#475569;">Gerado</th>
            <th align="left" style="padding:10px 8px;font-size:12px;color:#475569;">Esperado</th>
            <th align="left" style="padding:10px 8px;font-size:12px;color:#475569;">Faixa normal</th>
            <th align="left" style="padding:10px 8px;font-size:12px;color:#475569;">Situacao</th>
            <th align="left" style="padding:10px 8px;font-size:12px;color:#475569;">Perda estimada</th>
          </tr>
        </thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
      <p style="margin:16px 0 0;font-size:12px;line-height:1.5;color:#64748b;">A faixa normal considera uma tolerancia de 10% sobre a geracao esperada para o dia.</p>
    </div>
  </body>
</html>"""

@dag(
    dag_id="weg_analysis",
    start_date=datetime(2026, 1, 1),
    schedule="@daily",
    catchup=False,
    tags=["weg", "daily", "LLM"],
)

def weg_analysis():
    #task 1: puxar dados cadastrais da usina
    get_plant_data = SQLExecuteQueryOperator(
        task_id="get_plant_data",
        conn_id="railway_postgres",
        sql=sql_query,
        handler=row_as_dict,
    )


    @task
    def get_credentials(plants):
        if plants[0]:
            return decrypt(plants[0].get('credentials_encrypted').get('ciphertext'), key)
    
    #task 2: puxar dados de telemetria da usina
    @task(retries=2, retry_delay=timedelta(seconds=5),retry_exponential_backoff=True,)
    def get_telemetry(plants, credentials):
        #salvar telemetria no banco de dados
        target_day = get_target_date()
        date_from_str, date_to_str = utc_day_window(target_day)

        base_params = {
            "dateFrom": date_from_str, #data de ontem 00:00
            "dateTo": date_to_str, #data de ontem 23:59
            "groupBy": 900000,
            "variables": "acActivePower",
        }

        headers = {
            "x-api-key": credentials.get('api_key'),
            "x-api-secret": credentials.get('api_secret'),
        }
        results = []
        with requests.Session() as session:
            session.headers.update(headers)

            for plant in plants:
                params = {
                    **base_params,
                    "plantId": plant.get('vendor_plant_id')
                }
                response = session.get(url=weg_url, params=params)
                if response.status_code == 429:
                    time.sleep(20)
                    response = session.get(url=weg_url, params=params)
                
                results.append({
                    "plant_id": plant.get('vendor_plant_id'),
                    "telemetry": response.json(),
                })
        return results
    
    #task 3: puxar dados climáticos
    @task
    def get_weather(plants):
        results = []
        target_day = get_target_date()
        with requests.Session() as session:
            for plant in plants:
                latitude = plant.get("latitude")
                longitude = plant.get("longitude")
                timezone = plant.get("timezone") or "America/Sao_Paulo"

                if latitude is None or longitude is None:
                    results.append({
                        "plant_id": plant.get("plant_id"),
                        "plant_name": plant.get("name"),
                        "error": "missing_latitude_or_longitude",
                    })
                    continue


                open_meteo_params = {
                    "latitude": latitude,
                    "longitude": longitude,
                    "hourly": ",".join(hourly_variables),
                    "daily": ",".join(daily_variables),
                    "timezone": timezone,
                    "start_date": target_day.isoformat(),
                    "end_date": target_day.isoformat(),
                }

                response = session.get(
                    open_meteo_url,
                    params=open_meteo_params,
                    timeout=30,
                )
                response.raise_for_status()
                open_meteo_data = response.json()

                results.append({
                    "plant_id": plant.get("plant_id"),
                    "vendor_plant_id": plant.get("vendor_plant_id"),
                    "plant_name": plant.get("name"),
                    "latitude": latitude,
                    "longitude": longitude,
                    "timezone": timezone,
                    "tilt_deg": plant.get("tilt_deg"),
                    "azimuth_deg": plant.get("azimuth_deg"),
                    "area_m2": plant.get("area_m2"),
                    "module_efficiency": plant.get("module_efficiency", 0.178),
                    "target_date": target_day.isoformat(),
                    "open_meteo_forecast": open_meteo_data,
                })

        return results
    
    @task
    def get_expected_generation(weather_results):
        #task 4: calcular geração esperada
        results = []
        for weather_result in weather_results:
            if weather_result.get("error"):
                results.append({
                    "plant_id": weather_result.get("plant_id"),
                    "plant_name": weather_result.get("plant_name"),
                    "error": weather_result.get("error"),
                })
                continue
            target_day = target_date_from_weather_result(weather_result)
            results.append(estimate_generation_kwh_from_open_meteo(weather_result, target_day))

        return results

    @task
    def analyze_generation(telemetry_results, expected_generation):
        #task 5 e 6: comparar geracao real x esperada e calcular perdas
        expected_by_vendor_plant = expected_data_by_vendor_plant(expected_generation)
        results = []
        for telemetry_result in telemetry_results:
            vendor_plant_id = str(telemetry_result.get("plant_id"))
            expected = expected_by_vendor_plant.get(vendor_plant_id, {})
            expected_data = expected.get("data") or {}
            expected_kwh = float(expected_data.get("expected_generation_kwh") or 0)
            measured_kwh = measured_generation_kwh(telemetry_result)
            min_expected = round(expected_kwh * (1 - expected_generation_tolerance), 3)
            max_expected = round(expected_kwh * (1 + expected_generation_tolerance), 3)
            loss_kwh = round(max(expected_kwh - measured_kwh, 0), 3)
            results.append({
                "plant_id": expected.get("plant_id"),
                "vendor_plant_id": vendor_plant_id,
                "plant_name": expected.get("plant_name") or vendor_plant_id,
                "target_date": expected_data.get("date"),
                "measured_generation_kwh": measured_kwh,
                "expected_generation_kwh": round(expected_kwh, 3),
                "expected_min_kwh": min_expected,
                "expected_max_kwh": max_expected,
                "within_expected_range": min_expected <= measured_kwh <= max_expected,
                "loss_kwh": loss_kwh,
                "loss_brl": round(loss_kwh * energy_price_brl_per_kwh, 2),
            })
        return results

    @task
    def generate_llm_report(analysis_results):
        #task 7: usa LLM quando OPENAI_API_KEY existir; caso contrario nao bloqueia o email
        fallback = simple_report(analysis_results)
        if not os.getenv("OPENAI_API_KEY"):
            return fallback
        try:
            client = OpenAI()
            response = client.responses.create(
                model=os.getenv("OPENAI_MODEL", "gpt-5-mini"),
                input=(
                    "Escreva um relatorio objetivo em portugues para o dono das usinas. "
                    "Use estes dados JSON e destaque perdas em kWh e reais:\n"
                    f"{json.dumps(analysis_results, ensure_ascii=False)}"
                ),
            )
            return response.output_text
        except Exception as exc:
            return f"{fallback}\n\nObservacao: relatorio LLM indisponivel ({exc})."

    @task
    def send_generation_email(analysis_results):
        #task 8: remetente vem de AIRFLOW__SMTP__SMTP_MAIL_FROM
        send_email_smtp(
            to=report_recipient_email,
            subject="Relatorio diario de geracao solar",
            html_content=build_generation_email_html(analysis_results),
        )

    credentials = get_credentials(get_plant_data.output)
    telemetry = get_telemetry(get_plant_data.output, credentials)
    weather = get_weather(get_plant_data.output)
    expected_generation = get_expected_generation(weather)
    analysis = analyze_generation(telemetry, expected_generation)
    send_generation_email(analysis)


weg_analysis()
