from rest_framework.permissions import BasePermission


class IsAdminOrContentManager(BasePermission):
    """
    Allow access for staff/superuser or users in content_manager group.
    """

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False

        if user.is_staff or user.is_superuser:
            return True

        return user.groups.filter(name="content_manager").exists()
