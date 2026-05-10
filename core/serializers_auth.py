from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

User = get_user_model()


class LearnerRegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(write_only=True, min_length=8, style={"input_type": "password"})
    password_confirm = serializers.CharField(write_only=True, style={"input_type": "password"})
    email = serializers.EmailField(required=False, allow_blank=True, default="")

    def validate_username(self, value):
        candidate = value.strip()
        if not candidate:
            raise serializers.ValidationError("Username tidak boleh kosong.")
        if User.objects.filter(username__iexact=candidate).exists():
            raise serializers.ValidationError("Username sudah dipakai.")
        return candidate

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Kata sandi tidak sama."})
        email = (attrs.get("email") or "").strip()
        attrs["email"] = email
        dummy = User(username=attrs["username"], email=email)
        try:
            validate_password(attrs["password"], user=dummy)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs
