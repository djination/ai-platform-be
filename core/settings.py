"""
Django settings for core project.
"""

import os
from pathlib import Path
from datetime import timedelta

from dotenv import load_dotenv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.getenv("DEBUG", "False").lower() == "true"

ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'corsheaders',
    'rest_framework',
    'content_engine',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'core.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'core.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": os.getenv("DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.getenv("DB_NAME", BASE_DIR / "db.sqlite3"),
        "USER": os.getenv("DB_USER", ""),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", ""),
        "PORT": os.getenv("DB_PORT", ""),
    }
}

# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = 'static/'

# Legacy fallback API key used by OpenClaw to call ingest endpoint.
# Prefer DB-backed `IngestAPIKey` entries for key rotation.
OPENCLAW_API_KEY = os.getenv('OPENCLAW_API_KEY', 'change-me-in-production')

# Phase 2 learner chat (OpenRouter-compatible HTTP API)
OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
OPENROUTER_MODEL = os.getenv('OPENROUTER_MODEL', 'openai/gpt-4o-mini')
OPENROUTER_BASE_URL = os.getenv(
    'OPENROUTER_BASE_URL',
    'https://openrouter.ai/api/v1/chat/completions',
)
CHAT_DAILY_MESSAGE_LIMIT = int(os.getenv('CHAT_DAILY_MESSAGE_LIMIT', '200'))
# Kuota chat harian per tier berbayar (0 = tanpa batas harian untuk tier itu). Throttle HTTP tetap berlaku.
CHAT_GO_DAILY_MESSAGE_LIMIT = int(os.getenv('CHAT_GO_DAILY_MESSAGE_LIMIT', '400'))
CHAT_PLUS_DAILY_MESSAGE_LIMIT = int(os.getenv('CHAT_PLUS_DAILY_MESSAGE_LIMIT', '2000'))
CHAT_PRO_DAILY_MESSAGE_LIMIT = int(os.getenv('CHAT_PRO_DAILY_MESSAGE_LIMIT', '0'))
# Kuota akses konten/modul harian per tier (0 = tanpa batas harian untuk tier itu).
CONTENT_DAILY_LIMIT = int(os.getenv('CONTENT_DAILY_LIMIT', '5'))
CONTENT_GO_DAILY_LIMIT = int(os.getenv('CONTENT_GO_DAILY_LIMIT', '15'))
CONTENT_PLUS_DAILY_LIMIT = int(os.getenv('CONTENT_PLUS_DAILY_LIMIT', '50'))
CONTENT_PRO_DAILY_LIMIT = int(os.getenv('CONTENT_PRO_DAILY_LIMIT', '0'))

# Simulasi pembayaran (demo). Matikan di production (false).
BILLING_DEMO_PAYMENT_ENABLED = os.getenv('BILLING_DEMO_PAYMENT_ENABLED', 'false').lower() in (
    '1',
    'true',
    'yes',
)
BILLING_DEMO_SUBSCRIPTION_DAYS = int(os.getenv('BILLING_DEMO_SUBSCRIPTION_DAYS', '30'))

CHAT_REPLY_CACHE_ENABLED = os.getenv('CHAT_REPLY_CACHE_ENABLED', 'true').lower() in (
    '1',
    'true',
    'yes',
)

# Admin content discovery (search + fetch + ingest).
SERPAPI_API_KEY = os.getenv('SERPAPI_API_KEY', '')
# Google Custom Search JSON API: https://developers.google.com/custom-search/v1/overview
# GOOGLE_API_KEY is accepted as fallback when GOOGLE_CSE_API_KEY is empty.
GOOGLE_CSE_API_KEY = (
    os.getenv('GOOGLE_CSE_API_KEY', '') or os.getenv('GOOGLE_API_KEY', '')
).strip()
GOOGLE_CSE_CX = os.getenv('GOOGLE_CSE_CX', '').strip()
DISCOVERY_SEARCH_BACKEND = os.getenv('DISCOVERY_SEARCH_BACKEND', 'duckduckgo').strip().lower()
DISCOVERY_MAX_RESULTS_CAP = int(os.getenv('DISCOVERY_MAX_RESULTS_CAP', '15'))
DISCOVERY_MIN_EXTRACTED_CHARS = int(os.getenv('DISCOVERY_MIN_EXTRACTED_CHARS', '300'))
DISCOVERY_HTTP_TIMEOUT_SECONDS = int(os.getenv('DISCOVERY_HTTP_TIMEOUT_SECONDS', '25'))
DISCOVERY_HTTP_USER_AGENT = os.getenv(
    'DISCOVERY_HTTP_USER_AGENT',
    'Mozilla/5.0 (compatible; EduPlatformContentBot/1.0)',
)
DISCOVERY_FAILED_URL_CACHE_SECONDS = int(os.getenv('DISCOVERY_FAILED_URL_CACHE_SECONDS', '604800'))

# Google reCAPTCHA (v2 checkbox or v3 — siteverify supports both). When RECAPTCHA_SECRET_KEY is set,
# POST /api/auth/register/ requires a valid recaptcha_token.
RECAPTCHA_SECRET_KEY = os.getenv('RECAPTCHA_SECRET_KEY', '').strip()
RECAPTCHA_MIN_SCORE = float(os.getenv('RECAPTCHA_MIN_SCORE', '0.5'))
# If true (and RECAPTCHA_SECRET_KEY set), POST /api/auth/token/ requires recaptcha_token (v3 recommended).
RECAPTCHA_VERIFY_LOGIN = os.getenv('RECAPTCHA_VERIFY_LOGIN', 'false').lower() in ('1', 'true', 'yes')

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.ScopedRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'openclaw_ingest': os.getenv('OPENCLAW_INGEST_THROTTLE_RATE', '30/minute'),
        'chat': os.getenv('CHAT_THROTTLE_RATE', '60/hour'),
        'discover_ingest': os.getenv('DISCOVERY_INGEST_THROTTLE_RATE', '12/hour'),
        'learner_register': os.getenv('LEARNER_REGISTER_THROTTLE_RATE', '20/hour'),
        'learner_login': os.getenv('LEARNER_LOGIN_THROTTLE_RATE', '30/hour'),
        'billing_demo': os.getenv('BILLING_DEMO_THROTTLE_RATE', '30/hour'),
        'billing_manage': os.getenv('BILLING_MANAGE_THROTTLE_RATE', '30/hour'),
    },
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(
        minutes=int(os.getenv('JWT_ACCESS_LIFETIME_MINUTES', '15'))
    ),
    'REFRESH_TOKEN_LIFETIME': timedelta(
        days=int(os.getenv('JWT_REFRESH_LIFETIME_DAYS', '7'))
    ),
    'AUTH_HEADER_TYPES': ('Bearer',),
}

CORS_ALLOWED_ORIGINS = [
    'http://localhost:5173',
    'http://127.0.0.1:5173',
]

# CSRF_TRUSTED_ORIGINS = os.getenv("CSRF_TRUSTED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")