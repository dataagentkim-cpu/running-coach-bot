# Garmin 최초 1회 인증 셋업 — MFA 코드 입력 후 토큰 저장
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

GARTH_HOME = Path.home() / ".garth_runcoach"


def main() -> None:
    from garminconnect import Garmin

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        print("❌ .env에 GARMIN_EMAIL / GARMIN_PASSWORD 설정 필요")
        return

    print(f"Garmin Connect 로그인: {email}")
    print("※ 이메일/SMS로 MFA 코드가 발송되면 여기에 입력해.")

    def get_mfa() -> str:
        return input("🔐 MFA 코드 입력: ").strip()

    try:
        api = Garmin(email, password, prompt_mfa=get_mfa)
        api.login(tokenstore=str(GARTH_HOME))
        name = api.get_full_name()
        print(f"\n✅ 로그인 성공! 환영해요, {name}!")
        print(f"토큰 저장됨: {GARTH_HOME}")
        print("\n이제 python main.py sync 로 데이터를 가져올 수 있어.")
    except Exception as e:
        print(f"\n❌ 로그인 실패: {e}")


if __name__ == "__main__":
    main()
