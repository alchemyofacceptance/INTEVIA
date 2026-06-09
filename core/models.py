from django.db import models
from django.contrib.auth.models import User

# ---------------------------------------------------------
# Profile
# ---------------------------------------------------------
# Extends Django's built-in User model with INTEVIA-specific
# identity fields. This is the "human identity" anchor for
# everything else in the platform.
# ---------------------------------------------------------
class Profile(models.Model):
    # One-to-one link to Django's User object.
    # This keeps authentication separate from domain identity.
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    # Display name shown in the UI.
    # Rule of 9s → 90 chars for identity fields.
    display_name = models.CharField(max_length=90, blank=True)

    # Timestamp for continuity layer.
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        # Fallback to username if no display name set.
        return self.display_name or self.user.username


# ---------------------------------------------------------
# Role
# ---------------------------------------------------------
# Represents a functional or conceptual role within INTEVIA.
# Examples:
#   - User (the human)
#   - Gee  (meaning layer)
#   - Coe  (implementation layer)
#
# This model is intentionally flexible so the ontology can
# evolve (e.g., Mentor, Reviewer, Observer).
# ---------------------------------------------------------
class Role(models.Model):
    # Name of the role (unique).
    # Rule of 9s → 90 chars.
    name = models.CharField(max_length=90, unique=True)

    # Optional description for clarity.
    # Rule of 9s → 270 chars (3× identity length).
    description = models.CharField(max_length=270, blank=True)

    # Timestamp for continuity layer.
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


# ---------------------------------------------------------
# ProfileRole (Bridging Table)
# ---------------------------------------------------------
# Many-to-many relationship between Profile and Role.
# This is the "organ system" that allows a Profile to hold
# multiple roles and roles to be assigned to many profiles.
#
# We use an explicit bridging model instead of Django's
# auto-generated M2M so we can:
#   - store metadata (assigned_at)
#   - enforce uniqueness
#   - extend later if needed
# ---------------------------------------------------------
class ProfileRole(models.Model):
    # Link to Profile (the human identity).
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)

    # Link to Role (the conceptual function).
    role = models.ForeignKey(Role, on_delete=models.CASCADE)

    # Timestamp for continuity layer.
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Prevent duplicate assignments.
        unique_together = ('profile', 'role')

    def __str__(self):
        return f"{self.profile} → {self.role}"
