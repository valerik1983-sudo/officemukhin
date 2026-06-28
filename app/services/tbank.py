import hashlib
import requests
from typing import Dict, Any, Optional

from ..config import TBANK_TERMINAL_KEY, TBANK_SECRET_KEY, TBANK_WEBHOOK_URL

TBANK_API_URL = "https://securepay.tinkoff.ru/v2/"


def generate_token(params: Dict[str, Any], password: str) -> str:
    """
    Генерирует токен для запроса к T‑Банк.
    
    Алгоритм:
    1. Убираем пустые значения и поле Token
    2. Сортируем ключи по алфавиту
    3. Формируем строку вида "Key1Value1Key2Value2..."
    4. Добавляем пароль в конец
    5. Вычисляем SHA-256 хеш в верхнем регистре
    """
    # 1. Убираем пустые значения и Token (если он уже есть)
    filtered = {k: v for k, v in params.items() if v is not None and v != "" and k != "Token"}
    
    # 2. Сортируем ключи по алфавиту и собираем строку
    sorted_keys = sorted(filtered.keys())
    data_string = "".join([f"{k}{filtered[k]}" for k in sorted_keys])
    
    # 3. Добавляем пароль в конец
    data_string += password
    
    # 4. Вычисляем SHA-256 хеш в верхнем регистре
    return hashlib.sha256(data_string.encode("utf-8")).hexdigest().upper()


def create_payment(
    amount: int,
    order_id: str,
    description: str,
    success_url: str,
    fail_url: str,
    client_tg_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Создает платеж через T‑Банк.
    
    Args:
        amount: Сумма в КОПЕЙКАХ (например, 5000 ₽ = 500000)
        order_id: Уникальный ID заказа в вашей системе
        description: Описание платежа
        success_url: URL для редиректа при успешной оплате
        fail_url: URL для редиректа при неудачной оплате
        client_tg_id: Telegram ID клиента (передается в DATA)
    
    Returns:
        Словарь с данными платежа:
            - payment_id: ID платежа в T‑Банк
            - payment_url: Ссылка на оплату
    
    Raises:
        Exception: Если T‑Банк вернул ошибку
    """
    payload = {
        "TerminalKey": TBANK_TERMINAL_KEY,
        "Amount": amount,
        "OrderId": order_id,
        "Description": description[:100],  # T‑Банк ограничивает длину
        "SuccessURL": success_url,
        "FailURL": fail_url,
        "NotificationURL": TBANK_WEBHOOK_URL,
        "DATA": {
            "TelegramUserId": str(client_tg_id) if client_tg_id else ""
        }
    }
    
    # Генерируем подпись
    payload["Token"] = generate_token(payload, TBANK_SECRET_KEY)
    
    # Отправляем запрос
    try:
        response = requests.post(
            f"{TBANK_API_URL}Init",
            json=payload,
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        raise Exception(f"Ошибка подключения к T‑Банк: {str(e)}")
    
    if data.get("Success"):
        return {
            "payment_id": order_id,  # Используем наш OrderId как ID платежа
            "payment_url": data["PaymentURL"]
        }
    else:
        error_msg = data.get("Message", "Неизвестная ошибка T‑Банк")
        error_details = data.get("Details", "")
        raise Exception(f"T‑Банк ошибка: {error_msg}. {error_details}")


def check_payment_status(order_id: str) -> str:
    """
    Проверяет статус платежа в T‑Банк.
    
    Args:
        order_id: ID заказа (OrderId)
    
    Returns:
        Статус платежа: "NEW", "FORM_SHOWED", "DEADLINE_EXPIRED", 
                        "CANCELED", "PREAUTHORIZING", "AUTHORIZING", 
                        "AUTHORIZED", "AUTHORIZATION_CANCELED", 
                        "REJECTED", "3DS_CHECKING", "3DS_CHECKED", 
                        "PAY_CHECKING", "CHECKED", "REFUNDING", 
                        "REFUNDED", "CONFIRMED"
    """
    payload = {
        "TerminalKey": TBANK_TERMINAL_KEY,
        "OrderId": order_id
    }
    payload["Token"] = generate_token(payload, TBANK_SECRET_KEY)
    
    try:
        response = requests.post(
            f"{TBANK_API_URL}GetState",
            json=payload,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        return data.get("Status", "UNKNOWN")
    except Exception:
        return "UNKNOWN"


def verify_webhook_signature(data: Dict[str, Any]) -> bool:
    """
    Проверяет подпись уведомления от T‑Банк.
    
    T‑Банк присылает поле Token, которое нужно проверить.
    """
    # Копируем данные и удаляем Token
    params = data.copy()
    token = params.pop("Token", None)
    
    if not token:
        return False
    
    # Генерируем ожидаемый токен
    expected_token = generate_token(params, TBANK_SECRET_KEY)
    
    return token == expected_token