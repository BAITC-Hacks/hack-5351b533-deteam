"""Телефония: AudioSocket-сервер для Asterisk + REST регистрации звонков /api/telephony/*."""
import os
from fastapi import APIRouter

router = APIRouter()
AUDIOSOCKET_PORT = int(os.getenv("AUDIOSOCKET_PORT", "9092"))

async def start_audiosocket():
    return None
