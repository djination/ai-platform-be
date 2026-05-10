from django.contrib.auth import get_user_model
from django.urls import path
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenRefreshView

from .auth_jwt_views import LearnerTokenObtainPairView
from .recaptcha import recaptcha_is_configured, verify_recaptcha_token
from .serializers_auth import LearnerRegisterSerializer

User = get_user_model()


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def me_view(request):
    return Response(
        {
            "id": request.user.id,
            "username": request.user.username,
            "is_staff": request.user.is_staff,
            "is_superuser": request.user.is_superuser,
        }
    )


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([ScopedRateThrottle])
def register_learner(request):
    """
    Public signup for learner accounts (non-staff Django users).
    Staff / superuser accounts must be created via Django admin or manage.py.
    """
    serializer = LearnerRegisterSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    if recaptcha_is_configured():
        token = ""
        if isinstance(request.data, dict):
            token = str(request.data.get("recaptcha_token") or "").strip()
        xfwd = request.META.get("HTTP_X_FORWARDED_FOR")
        remote_ip = (xfwd.split(",")[0].strip() if xfwd else None) or request.META.get(
            "REMOTE_ADDR"
        )
        ok, err = verify_recaptcha_token(token, remote_ip=remote_ip)
        if not ok:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

    data = serializer.validated_data
    user = User.objects.create_user(
        username=data["username"],
        email=data.get("email") or "",
        password=data["password"],
        is_staff=False,
        is_superuser=False,
    )
    return Response(
        {
            "id": user.id,
            "username": user.username,
            "message": "Akun berhasil dibuat. Silakan masuk dengan username dan kata sandi Anda.",
        },
        status=status.HTTP_201_CREATED,
    )


register_learner.throttle_scope = "learner_register"


urlpatterns = [
    path("token/", LearnerTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("register/", register_learner, name="auth_register_learner"),
    path("me/", me_view, name="auth_me"),
]
