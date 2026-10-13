from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

OPEN_METEO_ATTRIBUTION = "Datos meteorologicos por Open-Meteo.com"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class GeoLocation:
    name: str
    latitude: float
    longitude: float
    timezone: str
    country: str | None = None
    admin1: str | None = None

    @property
    def label(self) -> str:
        parts = [self.name, self.admin1, self.country]
        return ", ".join(part for part in parts if part)


class WeatherService:
    def __init__(
        self,
        database_path: Path,
        *,
        cache_minutes: int = 45,
        timeout: float = 8.0,
    ) -> None:
        self.database_path = database_path
        self.cache_minutes = cache_minutes
        self.timeout = timeout
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS weather_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    async def geocode(self, city: str) -> GeoLocation | None:
        clean = city.strip()
        if len(clean) < 2:
            return None
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                GEOCODING_URL,
                params={"name": clean, "count": 1, "language": "es", "format": "json"},
            )
            response.raise_for_status()
            results = response.json().get("results") or []
        if not results:
            return None
        item = results[0]
        return GeoLocation(
            name=item["name"],
            latitude=float(item["latitude"]),
            longitude=float(item["longitude"]),
            timezone=item.get("timezone") or "auto",
            country=item.get("country"),
            admin1=item.get("admin1"),
        )

    async def current_forecast(
        self,
        *,
        latitude: float,
        longitude: float,
        timezone: str = "auto",
        label: str | None = None,
    ) -> dict[str, Any] | None:
        cache_key = f"{latitude:.4f}:{longitude:.4f}:{timezone}"
        cached = self._cached(cache_key)
        if cached:
            return cached
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(
                FORECAST_URL,
                params={
                    "latitude": latitude,
                    "longitude": longitude,
                    "timezone": timezone or "auto",
                    "current": "temperature_2m,weather_code,is_day",
                    "hourly": "precipitation_probability",
                    "forecast_days": 1,
                },
            )
            response.raise_for_status()
            raw = response.json()
        current = raw.get("current") or {}
        hourly = raw.get("hourly") or {}
        precipitation = self._nearest_precipitation(current.get("time"), hourly)
        payload = {
            "location": label,
            "temperature": current.get("temperature_2m"),
            "temperature_unit": (raw.get("current_units") or {}).get("temperature_2m", "°C"),
            "condition": weather_code_label(current.get("weather_code")),
            "weather_code": current.get("weather_code"),
            "precipitation_probability": precipitation,
            "updated_at": current.get("time") or datetime.now(UTC).isoformat(),
            "source": OPEN_METEO_ATTRIBUTION,
            "source_url": "https://open-meteo.com/",
        }
        self._store_cache(cache_key, payload)
        return payload

    def _nearest_precipitation(
        self,
        current_time: str | None,
        hourly: dict[str, Any],
    ) -> int | None:
        times = hourly.get("time") or []
        values = hourly.get("precipitation_probability") or []
        if not times or not values:
            return None
        if current_time in times:
            return values[times.index(current_time)]
        return values[0]

    def _cached(self, cache_key: str) -> dict[str, Any] | None:
        max_age = datetime.now(UTC) - timedelta(minutes=self.cache_minutes)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM weather_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
        if not row:
            return None
        created_at = datetime.fromisoformat(row["created_at"])
        if created_at < max_age:
            return None
        return json.loads(row["payload"])

    def _store_cache(self, cache_key: str, payload: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO weather_cache (cache_key, payload, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    created_at = excluded.created_at
                """,
                (cache_key, json.dumps(payload), datetime.now(UTC).isoformat()),
            )


def weather_code_label(code: int | None) -> str | None:
    if code is None:
        return None
    labels = {
        0: "cielo despejado",
        1: "principalmente despejado",
        2: "parcialmente nublado",
        3: "nublado",
        45: "niebla",
        48: "niebla con escarcha",
        51: "llovizna ligera",
        53: "llovizna moderada",
        55: "llovizna intensa",
        61: "lluvia ligera",
        63: "lluvia moderada",
        65: "lluvia intensa",
        71: "nieve ligera",
        73: "nieve moderada",
        75: "nieve intensa",
        80: "chubascos ligeros",
        81: "chubascos moderados",
        82: "chubascos intensos",
        95: "tormenta",
        96: "tormenta con granizo ligero",
        99: "tormenta con granizo intenso",
    }
    return labels.get(code, f"codigo meteorologico {code}")
