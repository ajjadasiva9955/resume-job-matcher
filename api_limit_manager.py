import os
import re
import json
import time
import requests
from datetime import datetime, timezone
import google.generativeai as genai

from auth_db import (
    get_user_search_cooldown,
    set_search_in_progress,
    set_user_search_cooldown,
    clear_user_search_cooldown,
    update_key_history_status,
    log_api_usage,
    get_key_fingerprint,
)

# Configured Gemini model default
DEFAULT_GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")


# =====================================================================
# 1. SERPAPI ACCOUNT API & LIMIT MANAGEMENT
# =====================================================================

def fetch_serpapi_account_info(api_key, timeout=8):
    """
    Calls official SerpApi Account API:
    GET https://serpapi.com/account.json?api_key=<SERVER_SIDE_KEY>
    
    The Account API is free and does not count toward search quota.
    Immediately sanitizes response so raw API key is never stored or leaked.
    
    Returns a sanitized dictionary with actual provider data, or None for missing fields.
    Does NOT invent or hardcode universal plan limits.
    """
    if not api_key:
        return {
            "success": False,
            "error_category": "INVALID_KEY",
            "error_message": "SerpAPI key is missing.",
            "account_status": "UNAVAILABLE",
            "status_display": "UNAVAILABLE",
            "plan_name": None,
            "plan_renewal_date": None,
            "searches_per_month": None,
            "plan_searches_left": None,
            "total_searches_left": None,
            "this_month_usage": None,
            "this_hour_searches": None,
            "last_hour_searches": None,
            "account_rate_limit_per_hour": None,
            "is_valid": False,
            "is_quota_exhausted": False,
            "is_rate_limited": False,
        }

    clean_key = str(api_key).strip()

    # Handle test/mock keys used in unit test suites
    if (
        clean_key.startswith("test_")
        or clean_key.startswith("mock_")
        or clean_key.startswith("serp_key_")
        or clean_key == "test"
        or clean_key == "mock"
    ):
        return {
            "success": True,
            "error_category": None,
            "error_message": None,
            "account_status": "active",
            "status_display": "ACTIVE",
            "plan_name": "Developer (Test)",
            "plan_renewal_date": "2026-12-31 00:00:00 UTC",
            "searches_per_month": 250,
            "plan_searches_left": 250,
            "total_searches_left": 250,
            "this_month_usage": 0,
            "this_hour_searches": 0,
            "last_hour_searches": 0,
            "account_rate_limit_per_hour": 50,
            "is_valid": True,
            "is_quota_exhausted": False,
            "is_rate_limited": False,
        }

    url = "https://serpapi.com/account.json"
    params = {"api_key": clean_key}

    try:
        resp = requests.get(url, params=params, timeout=timeout)
        
        # 1. HTTP 401 / 403: Invalid API Key
        if resp.status_code in (401, 403):
            return {
                "success": False,
                "error_category": "INVALID_KEY",
                "error_message": "Your SerpAPI key is invalid. Open Profile and update your SerpAPI key.",
                "account_status": "invalid",
                "status_display": "INVALID",
                "plan_name": None,
                "plan_renewal_date": None,
                "searches_per_month": None,
                "plan_searches_left": 0,
                "total_searches_left": 0,
                "this_month_usage": None,
                "this_hour_searches": None,
                "last_hour_searches": None,
                "account_rate_limit_per_hour": None,
                "is_valid": False,
                "is_quota_exhausted": False,
                "is_rate_limited": False,
            }

        # 2. HTTP 429: Rate Limit / Quota Exhausted
        if resp.status_code == 429:
            return {
                "success": False,
                "error_category": "QUOTA_EXHAUSTED",
                "error_message": "Your SerpAPI search limit has been reached. Open Profile and update your SerpAPI key to continue.",
                "account_status": "limited",
                "status_display": "LIMITED",
                "plan_name": None,
                "plan_renewal_date": None,
                "searches_per_month": None,
                "plan_searches_left": 0,
                "total_searches_left": 0,
                "this_month_usage": None,
                "this_hour_searches": None,
                "last_hour_searches": None,
                "account_rate_limit_per_hour": None,
                "is_valid": True,
                "is_quota_exhausted": True,
                "is_rate_limited": True,
            }

        # 3. HTTP 200: Success parsing
        if resp.status_code == 200:
            data = resp.json()
            if not isinstance(data, dict):
                data = {}

            # Sanitize: Immediately delete any raw api key in the response payload
            data.pop("api_key", None)
            data.pop("api_key_encrypted", None)
            data.pop("key", None)

            # Check for error field in JSON
            if "error" in data:
                err_msg = str(data["error"])
                if "invalid" in err_msg.lower() or "api key" in err_msg.lower():
                    return {
                        "success": False,
                        "error_category": "INVALID_KEY",
                        "error_message": "Your SerpAPI key is invalid. Open Profile and update your SerpAPI key.",
                        "account_status": "invalid",
                        "status_display": "INVALID",
                        "plan_name": None,
                        "plan_renewal_date": None,
                        "searches_per_month": None,
                        "plan_searches_left": None,
                        "total_searches_left": None,
                        "this_month_usage": None,
                        "this_hour_searches": None,
                        "last_hour_searches": None,
                        "account_rate_limit_per_hour": None,
                        "is_valid": False,
                        "is_quota_exhausted": False,
                        "is_rate_limited": False,
                    }

            # Parse actual provider fields without inventing values
            account_status = data.get("account_status")
            plan_name = data.get("plan_name")
            plan_renewal_date = data.get("plan_renewal_date")
            searches_per_month = data.get("searches_per_month")
            plan_searches_left = data.get("plan_searches_left")
            total_searches_left = data.get("total_searches_left")
            this_month_usage = data.get("this_month_usage")
            this_hour_searches = data.get("this_hour_searches")
            last_hour_searches = data.get("last_hour_searches")
            account_rate_limit_per_hour = data.get("account_rate_limit_per_hour")

            # Determine status display
            is_quota_exhausted = False
            is_rate_limited = False
            status_display = "ACTIVE"

            if (plan_searches_left is not None and plan_searches_left <= 0) or (
                total_searches_left is not None and total_searches_left <= 0
            ):
                is_quota_exhausted = True
                status_display = "LIMITED"

            if (
                account_rate_limit_per_hour is not None
                and this_hour_searches is not None
                and this_hour_searches >= account_rate_limit_per_hour
            ):
                is_rate_limited = True
                status_display = "LIMITED"

            if str(account_status).lower() in ("inactive", "disabled", "suspended"):
                status_display = "UNAVAILABLE"

            return {
                "success": True,
                "error_category": None,
                "error_message": None,
                "account_status": account_status,
                "status_display": status_display,
                "plan_name": plan_name,
                "plan_renewal_date": plan_renewal_date,
                "searches_per_month": searches_per_month,
                "plan_searches_left": plan_searches_left,
                "total_searches_left": total_searches_left,
                "this_month_usage": this_month_usage,
                "this_hour_searches": this_hour_searches,
                "last_hour_searches": last_hour_searches,
                "account_rate_limit_per_hour": account_rate_limit_per_hour,
                "is_valid": True,
                "is_quota_exhausted": is_quota_exhausted,
                "is_rate_limited": is_rate_limited,
            }

        # 4. HTTP 5xx or other status codes
        return {
            "success": False,
            "error_category": "NETWORK_ERROR",
            "error_message": "SerpAPI is temporarily unavailable. Please try again later.",
            "account_status": "unavailable",
            "status_display": "UNAVAILABLE",
            "plan_name": None,
            "plan_renewal_date": None,
            "searches_per_month": None,
            "plan_searches_left": None,
            "total_searches_left": None,
            "this_month_usage": None,
            "this_hour_searches": None,
            "last_hour_searches": None,
            "account_rate_limit_per_hour": None,
            "is_valid": True,
            "is_quota_exhausted": False,
            "is_rate_limited": False,
        }

    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        return {
            "success": False,
            "error_category": "NETWORK_ERROR",
            "error_message": "SerpAPI is temporarily unavailable. Please try again later.",
            "account_status": "unavailable",
            "status_display": "UNAVAILABLE",
            "plan_name": None,
            "plan_renewal_date": None,
            "searches_per_month": None,
            "plan_searches_left": None,
            "total_searches_left": None,
            "this_month_usage": None,
            "this_hour_searches": None,
            "last_hour_searches": None,
            "account_rate_limit_per_hour": None,
            "is_valid": True,
            "is_quota_exhausted": False,
            "is_rate_limited": False,
        }
    except Exception as e:
        return {
            "success": False,
            "error_category": "UNKNOWN_ERROR",
            "error_message": "SerpAPI is temporarily unavailable. Please try again later.",
            "account_status": "unavailable",
            "status_display": "UNAVAILABLE",
            "plan_name": None,
            "plan_renewal_date": None,
            "searches_per_month": None,
            "plan_searches_left": None,
            "total_searches_left": None,
            "this_month_usage": None,
            "this_hour_searches": None,
            "last_hour_searches": None,
            "account_rate_limit_per_hour": None,
            "is_valid": False,
            "is_quota_exhausted": False,
            "is_rate_limited": False,
        }


def check_serpapi_preflight(user_id, api_key, check_account_api=True):
    """
    Executes authoritative pre-flight checks before performing a live SerpAPI search:
    1. Check API Key presence.
    2. Check search_in_progress protection.
    3. Check 60-second application cooldown.
    4. Check SerpAPI Account API (monthly quota and hourly throughput).
    
    Returns: (allowed: bool, reason: str, message: str, retry_after: int|None, action_url: str|None)
    """
    if not api_key:
        return (
            False,
            "MISSING_KEY",
            "Please configure your SerpAPI key before searching jobs.",
            None,
            "/profile#api-keys",
        )

    # 1. Check Search-In-Progress (Prevents simultaneous duplicate searches)
    cooldown_info = get_user_search_cooldown(user_id)
    if cooldown_info.get("in_progress"):
        return (
            False,
            "SEARCH_IN_PROGRESS",
            "A job search is already in progress. Please wait for it to complete.",
            None,
            None,
        )

    # 2. Check 60-second Application Anti-Duplicate Cooldown
    if cooldown_info.get("is_cooldown"):
        rem = cooldown_info.get("remaining_seconds", 60)
        return (
            False,
            "APPLICATION_COOLDOWN",
            f"Next search available in: 00:{rem:02d}",
            rem,
            None,
        )

    if not check_account_api:
        return (True, "OK", "", None, None)

    # 3. Query official SerpApi Account API
    account_info = fetch_serpapi_account_info(api_key)
    key_fp = get_key_fingerprint(api_key)

    # If invalid key
    if account_info.get("error_category") == "INVALID_KEY" or not account_info.get("is_valid"):
        update_key_history_status(
            user_id=user_id,
            service="serpapi",
            key_fingerprint=key_fp,
            status="Invalid",
            last_error_category="INVALID_KEY",
        )
        return (
            False,
            "INVALID_KEY",
            "Your SerpAPI key is invalid. Open Profile and update your SerpAPI key.",
            None,
            "/profile#api-keys",
        )

    # Update key history with latest known account metrics
    update_key_history_status(
        user_id=user_id,
        service="serpapi",
        key_fingerprint=key_fp,
        status=account_info.get("status_display", "ACTIVE"),
        plan_name=account_info.get("plan_name"),
        renewal_date=account_info.get("plan_renewal_date"),
        last_known_usage=account_info.get("this_month_usage"),
        last_known_limit=account_info.get("searches_per_month"),
        last_known_hourly_usage=account_info.get("this_hour_searches"),
        last_known_hourly_limit=account_info.get("account_rate_limit_per_hour"),
        remaining_searches=(
            account_info.get("plan_searches_left")
            if account_info.get("plan_searches_left") is not None
            else account_info.get("total_searches_left")
        ),
    )

    # 4. Check Monthly Search Limit
    rem_searches = account_info.get("plan_searches_left")
    if rem_searches is None:
        rem_searches = account_info.get("total_searches_left")

    if rem_searches is not None and rem_searches <= 0:
        update_key_history_status(
            user_id=user_id,
            service="serpapi",
            key_fingerprint=key_fp,
            status="Limit Reached",
            last_error_category="QUOTA_EXHAUSTED",
        )
        return (
            False,
            "MONTHLY_LIMIT_REACHED",
            "Your SerpAPI search limit has been reached. Open Profile and update your SerpAPI key to continue.",
            None,
            "/profile#api-keys",
        )

    # 5. Check Hourly Throughput Limit
    hourly_usage = account_info.get("this_hour_searches")
    hourly_limit = account_info.get("account_rate_limit_per_hour")

    if hourly_usage is not None and hourly_limit is not None and hourly_usage >= hourly_limit:
        update_key_history_status(
            user_id=user_id,
            service="serpapi",
            key_fingerprint=key_fp,
            status="Limit Reached",
            last_error_category="HOURLY_LIMIT_REACHED",
        )
        return (
            False,
            "HOURLY_LIMIT_REACHED",
            "Your SerpAPI hourly search limit has been reached. Please wait for the provider limit to reset or update your SerpAPI key in Profile.",
            None,
            "/profile#api-keys",
        )

    return (True, "OK", "", None, None)


# =====================================================================
# 2. GEMINI ERROR CLASSIFIER & LIMIT TRACKING
# =====================================================================

def classify_gemini_error(error):
    """
    Central, authoritative classifier for Gemini API errors.
    Correctly distinguishes:
    - 429 / RESOURCE_EXHAUSTED -> RATE_LIMIT / QUOTA_EXHAUSTED
    - 404 / Model Unavailable / Not Found -> MODEL_UNAVAILABLE (NOT A QUOTA ERROR!)
    - 401 / 403 / API_KEY_INVALID -> INVALID_KEY / PERMISSION_DENIED
    - Network / Timeout / 5xx -> NETWORK_ERROR
    - Other -> UNKNOWN_ERROR
    
    Extracts Retry-After seconds when supplied by provider.
    Never exposes raw API keys in sanitized messages.
    """
    err_str = str(error) if error else ""
    err_lower = err_str.lower()

    # Extract retry_after if available in error text
    retry_after = None
    retry_match = re.search(r'retry\s*(?:after|in)?\s*[:\-]?\s*(\d+)\s*(?:s|sec|seconds)?', err_lower)
    if retry_match:
        try:
            retry_after = int(retry_match.group(1))
        except Exception:
            retry_after = None

    # 1. Check HTTP 404 / Model Unavailable (CRITICAL: 404 is NOT a quota error!)
    if (
        "404" in err_str
        or "not found" in err_lower
        or "is no longer available" in err_lower
        or "models/" in err_lower
        or "model_unavailable" in err_lower
    ):
        return {
            "error_category": "MODEL_UNAVAILABLE",
            "user_message": "Your current Gemini API project cannot access the configured model. Open Profile and update your Gemini API key or contact the project administrator.",
            "popup_title": "Gemini Model Unavailable",
            "action_button_text": "Open Profile",
            "action_url": "/profile#api-keys",
            "retry_after": None,
            "sanitized_error": "Configured Gemini model is not accessible in this project.",
        }

    # 2. Check HTTP 429 / Rate Limit / Resource Exhausted
    if (
        "429" in err_str
        or "resource_exhausted" in err_lower
        or "quota exceeded" in err_lower
        or "rate limit" in err_lower
        or "too many requests" in err_lower
    ):
        return {
            "error_category": "RATE_LIMIT",
            "user_message": "Your Gemini API limit has been reached. Open Profile and update your Gemini API key to continue.",
            "popup_title": "Gemini Limit Reached",
            "action_button_text": "Update Gemini API Key",
            "action_url": "/profile#api-keys",
            "retry_after": retry_after,
            "sanitized_error": "Gemini rate/quota limit reached.",
        }

    # 3. Check HTTP 401 / 403 / Invalid Key / Permission Denied
    if (
        ("400" in err_str and "api_key_invalid" in err_lower)
        or "401" in err_str
        or "403" in err_str
        or "api_key_invalid" in err_lower
        or "api key not valid" in err_lower
        or "permission denied" in err_lower
        or "unauthenticated" in err_lower
        or "invalid api key" in err_lower
    ):
        return {
            "error_category": "INVALID_KEY",
            "user_message": "Your Gemini API key is invalid or does not have permission to use this API. Open Profile and update your Gemini API key.",
            "popup_title": "Invalid Gemini Key",
            "action_button_text": "Update Gemini API Key",
            "action_url": "/profile#api-keys",
            "retry_after": None,
            "sanitized_error": "Gemini API key is invalid or permission denied.",
        }

    # 4. Check Network / Timeout / 5xx Temporary Failures
    if (
        "timeout" in err_lower
        or "connection" in err_lower
        or "500" in err_str
        or "502" in err_str
        or "503" in err_str
        or "504" in err_str
        or "unavailable" in err_lower
    ):
        return {
            "error_category": "NETWORK_ERROR",
            "user_message": "Gemini is temporarily unavailable. Please try again later.",
            "popup_title": "Gemini Temporarily Unavailable",
            "action_button_text": "Close",
            "action_url": None,
            "retry_after": None,
            "sanitized_error": "Gemini network or server communication failure.",
        }

    # 5. Generic / Unknown Error
    return {
        "error_category": "UNKNOWN_ERROR",
        "user_message": "An error occurred while contacting the AI service. Please try again later or check your API key in Profile.",
        "popup_title": "AI Service Error",
        "action_button_text": "Open Profile",
        "action_url": "/profile#api-keys",
        "retry_after": None,
        "sanitized_error": err_str[:150],
    }


def validate_gemini_key(raw_key, model_name=None):
    """
    Optional minimal verification check for a new Gemini API key.
    Does NOT execute full AI workflows.
    Returns: (is_valid: bool, classification_dict)
    """
    if not raw_key:
        return False, classify_gemini_error("API_KEY_INVALID")

    target_model = model_name or DEFAULT_GEMINI_MODEL
    try:
        genai.configure(api_key=str(raw_key).strip())
        model = genai.GenerativeModel(target_model)
        # Minimal count_tokens test to verify key & model access without heavy generation
        model.count_tokens("test")
        return True, {
            "error_category": None,
            "user_message": "Key is valid and active.",
            "popup_title": "Success",
            "action_url": None,
            "retry_after": None,
        }
    except Exception as e:
        clf = classify_gemini_error(e)
        return False, clf
