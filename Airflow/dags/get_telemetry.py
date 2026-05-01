import json
import os
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pvlib
import requests
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.sdk import dag, task
from cryptography.fernet import Fernet

tz = ZoneInfo("America/Sao_Paulo")

key = os.getenv("CREDENTIALS_ENCRYPTION_KEY")
weg_url = "https://solarportal-api.weg.net/api/v1/measurements"
open_meteo_url = "https://api.open-meteo.com/v1/forecast"
default_tilt_deg = 10
default_azimuth_deg = 0
default_capacity_kwp = 17.1
default_loss_percent = 14
default_gamma_pdc = -0.004
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
                p.capacity_kwp,
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

def get_target_date():
    return (datetime.now(tz) - timedelta(days=1)).date()

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
    capacity_kwp = float_or_default(weather_result.get("capacity_kwp"), default_capacity_kwp)
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
    dc_power_w = pvlib.pvsystem.pvwatts_dc(
        poa["poa_global"],
        cell_temperature,
        capacity_kwp * 1000,
        default_gamma_pdc,
    )
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
            "capacity_kwp": round(float(capacity_kwp), 3),
            "source": "Open-Meteo irradiance + pvlib PVWatts",
        }
    }

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
            "dateFrom": date_from_str, #data de hoje 00:00
            "dateTo": date_to_str, #data de hoje 23:59
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
                    # response.raise_for_status()
                
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
                    "past_days": 1,
                    "forecast_days": 1,
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
                    "capacity_kwp": plant.get("capacity_kwp"),
                    "loss_percent": plant.get("loss_percent"),
                    "target_date": target_day.isoformat(),
                    "open_meteo_forecast": open_meteo_data,
                })

        return results
    
    @task
    def get_expected_generation(weather_results):
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

    credentials = get_credentials(get_plant_data.output)
    telemetry = get_telemetry(get_plant_data.output, credentials)
    weather = get_weather(get_plant_data.output)
    expected_generation = get_expected_generation(weather)


weg_analysis()
#task 4: calcular geração esperada
#task 5: Identificar se está dentro do intervalo esperado de geração
#task 6: estimar perda em kwh e financeira
#task 7: relatório da LLM
#task 8: mandar por email
