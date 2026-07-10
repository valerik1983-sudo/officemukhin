import hashlib
import json
import requests
from typing import Dict, Any, Optional, Tuple

from ..config import TBANK_TERMINAL_KEY, TBANK_SECRET_KEY, TBANK_WEBHOOK_URL

import logging

logger = logging.getLogger(__name__)

# Для боевого режима:
TBANK_API_URL = "https://securepay.tinkoff.ru/v2/"
# Для тестового режима (раскомментировать при необходимости):
# TBANK_API_URL = "https://rest-api-test.tinkoff.ru/v2/"

# Проверка SSL включена (используется системный набор сертификатов)
SSL_VERIFY = True  # <-- изменено с False на True


def generate_token(params: Dict[str, Any], password: str) -> Tuple[str, str]:
    sign_params = params.copy()
    sign_params["Password"] = password

    filtered = {}
    for k, v in sign_params.items():
        if k == "Token" or v is None or v == "" or isinstance(v, dict):
            continue
        filtered[k] = v

    sorted_keys = sorted(filtered.keys())
    data_string = "".join(str(filtered[k]) for k in sorted_keys)
    token = hashlib.sha256(data_string.encode("utf-8")).hexdigest().lower()
    return token, data_string


def _build_error_message(
    base_message: str,
    amount: int,
    order_id: str,
    description: str = "",
    sign_string: str = ""
) -> str:
    debug_info = (
        f"TerminalKey: {TBANK_TERMINAL_KEY}\n"
        f"SecretKey (маска): {TBANK_SECRET_KEY[:4]}...{TBANK_SECRET_KEY[-4:]}\n"
        f"API URL: {TBANK_API_URL}\n"
        f"Amount: {amount}\n"
        f"OrderId: {order_id}\n"
        f"Description: {description}"
    )
    if sign_string:
        debug_info += f"\n\nSIGN STRING: {sign_string}"
    return f"{base_message}\n\n{debug_info}"


def create_payment(
    amount: int,
    order_id: str,
    description: str,
    success_url: str,
    fail_url: str,
    client_tg_id: Optional[int] = None
) -> Dict[str, Any]:
    data_value = {}
    if client_tg_id:
        data_value["TelegramUserId"] = str(client_tg_id)

    payload = {
        "TerminalKey": TBANK_TERMINAL_KEY,
        "Amount": amount,
        "OrderId": order_id,
        "Description": description[:100],
        "SuccessURL": success_url,
        "FailURL": fail_url,
        "NotificationURL": TBANK_WEBHOOK_URL,
        "DATA": data_value
    }

    token, sign_string = generate_token(payload, TBANK_SECRET_KEY)
    payload["Token"] = token

    logger.debug(f"=== ОТЛАДКА T-БАНК (Init) ===")
    logger.debug(f"Payload: {json.dumps(payload, indent=2)}")

    try:
        response = requests.post(
            f"{TBANK_API_URL}Init",
            json=payload,
            timeout=15,
            verify=SSL_VERIFY  # <-- теперь True
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        raise Exception(
            _build_error_message(
                f"Ошибка подключения к T‑Банк: {str(e)}",
                amount,
                order_id,
                description,
                sign_string
            )
        )

    if data.get("Success"):
        return {
            "payment_id": data.get("PaymentId"),
            "payment_url": data["PaymentURL"]
        }
    else:
        error_msg = data.get("Message", "Неизвестная ошибка T‑Банк")
        error_details = data.get("Details", "")
        full_error = f"T‑Банк ошибка: {error_msg}. {error_details}"
        raise Exception(
            _build_error_message(
                full_error,
                amount,
                order_id,
                description,
                sign_string
            )
        )


def get_qr(
    payment_id: int,
    order_id: str,
    amount: int,
    description: str = "",
    data_type: str = "PAYLOAD"
) -> Dict[str, Any]:
    payload = {
        "TerminalKey": TBANK_TERMINAL_KEY,
        "PaymentId": payment_id,
        "DataType": data_type
    }

    token, sign_string = generate_token(payload, TBANK_SECRET_KEY)
    payload["Token"] = token

    logger.debug(f"=== ОТЛАДКА T-БАНК (GetQr) ===")
    logger.debug(f"Payload: {json.dumps(payload, indent=2)}")

    try:
        response = requests.post(
            f"{TBANK_API_URL}GetQr",
            json=payload,
            timeout=15,
            verify=SSL_VERIFY  # <-- теперь True
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        raise Exception(
            _build_error_message(
                f"Ошибка получения QR-кода: {str(e)}",
                amount,
                order_id,
                description,
                sign_string
            )
        )

    if data.get("Success"):
        return {
            "qr_data": data.get("Data"),
            "payment_id": payment_id
        }
    else:
        error_msg = data.get("Message", "Неизвестная ошибка")
        error_details = data.get("Details", "")
        full_error = f"Ошибка получения QR: {error_msg}. {error_details}"
        raise Exception(
            _build_error_message(
                full_error,
                amount,
                order_id,
                description,
                sign_string
            )
        )


def check_payment_status(order_id: str) -> str:
    payload = {
        "TerminalKey": TBANK_TERMINAL_KEY,
        "OrderId": order_id
    }
    token, _ = generate_token(payload, TBANK_SECRET_KEY)
    payload["Token"] = token

    try:
        response = requests.post(
            f"{TBANK_API_URL}GetState",
            json=payload,
            timeout=10,
            verify=SSL_VERIFY  # <-- теперь True
        )
        response.raise_for_status()
        data = response.json()
        return data.get("Status", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def verify_webhook_signature(data: Dict[str, Any]) -> bool:
    """
    Проверяет подпись вебхука от T‑Банк (входящие уведомления).
    """
    params = data.copy()
    token = params.pop("Token", None)
    if not token:
        return False

    params["Password"] = TBANK_SECRET_KEY

    filtered = {}
    for k, v in params.items():
        if v is None or v == "" or isinstance(v, dict):
            continue
        # === ПРЕОБРАЗОВАНИЕ БУЛЕВЫХ В НИЖНИЙ РЕГИСТР ===
        if isinstance(v, bool):
            filtered[k] = str(v).lower()
        else:
            filtered[k] = v

    sorted_keys = sorted(filtered.keys())
    data_string = "".join(str(filtered[k]) for k in sorted_keys)

    expected_token = hashlib.sha256(data_string.encode("utf-8")).hexdigest().lower()
    return token == expected_token