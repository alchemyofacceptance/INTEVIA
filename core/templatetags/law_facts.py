"""Database-free names for adoption facts already released to a template."""
from django import template

from src.intevia.services._record_names import adopting_body_name

register = template.Library()
register.simple_tag(adopting_body_name)
