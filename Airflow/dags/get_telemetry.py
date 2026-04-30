import json
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator
from airflow.sdk import dag, task
from cryptography.fernet import Fernet
from pvlib.iotools import get_nasa_power

tz = ZoneInfo("America/Sao_Paulo")

today = datetime.now(tz).date()
date_from = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=tz)
date_to = datetime(today.year, today.month, today.day, 23, 59, 0, tzinfo=tz)

date_from_str = date_from.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
date_to_str = date_to.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


key = os.getenv("CREDENTIALS_ENCRYPTION_KEY")
weg_url = "https://solarportal-api.weg.net/api/v1/measurements"
open_meteo_url = "https://api.open-meteo.com/v1/forecast"
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
                response.raise_for_status()
                
                results.append({
                    "plant_id": plant.get('vendor_plant_id'),
                    "telemetry": response.json(),
                })
        return results
    
    #task 3: puxar dados climáticos
    @task
    def get_weather(plants):
        results = []
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
                    "forecast_days": 2,
                }

                response = session.get(
                    open_meteo_url,
                    params=open_meteo_params,
                    timeout=30,
                )
                response.raise_for_status()
                open_meteo_data = response.json()

                # NASA POWER via pvlib: bom para baseline histórico solar/meteo.
                # Ajuste as datas conforme a janela da telemetria real.
                nasa_power_df, nasa_power_meta = get_nasa_power(
                    latitude=latitude,
                    longitude=longitude,
                    start=pd.Timestamp(date_from_str), #ajustar data
                    end=pd.Timestamp(date_to_str), #ajustar data
                    parameters=[
                        "ghi",
                        "dni",
                        "dhi",
                        "temp_air",
                        "wind_speed",
                    ],
                    community="re",
                )

                # nasa_power_df = nasa_power_df.reset_index()

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
                    "open_meteo_forecast": open_meteo_data,
                    "nasa_power_history": {
                        "metadata": nasa_power_meta,
                        "records": nasa_power_df.to_dict(orient="records"),
                    },
                })

        return results

    credentials = get_credentials(get_plant_data.output)
    telemetry = get_telemetry(get_plant_data.output, credentials)
    weather = get_weather(get_plant_data.output)

# new commit

weg_analysis()
#task 4: calcular geração esperada
#task 5: Identificar se está dentro do intervalo esperado de geração
#task 6: estimar perda em kwh e financeira
#task 7: relatório da LLM
#task 8: mandar por email
