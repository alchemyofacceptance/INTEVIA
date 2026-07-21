from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.forms import ProductAuthenticationForm
from core.models import Identity
from core.session import bind_identity_session
from src.intevia.services.event_read_service import (
	EventNotVisible,
	EventReadService,
)


@require_http_methods(["GET", "POST"])
def product_login(request: HttpRequest) -> HttpResponse:
	if request.user.is_authenticated:
		try:
			identity = Identity.objects.get(credential=request.user)
		except Identity.DoesNotExist:
			logout(request)
		else:
			if identity.access_state == Identity.AccessState.RESTRICTED:
				return redirect(reverse("restricted"))
			if identity.access_state == Identity.AccessState.ACTIVE:
				return redirect(reverse("shell"))

	form = ProductAuthenticationForm(request=request, data=request.POST or None)
	if request.method == "POST" and form.is_valid():
		credential = form.get_user()
		try:
			identity = Identity.objects.get(credential=credential)
		except (Identity.DoesNotExist, Identity.MultipleObjectsReturned):
			form.add_error(None, "Unable to sign in with those credentials.")
		else:
			if identity.access_state not in {
				Identity.AccessState.ACTIVE,
				Identity.AccessState.RESTRICTED,
			}:
				form.add_error(None, "Unable to sign in with those credentials.")
			else:
				login(request, credential)
				bind_identity_session(request, identity)
				if identity.access_state == Identity.AccessState.RESTRICTED:
					return redirect(reverse("restricted"))
				return redirect(reverse("shell"))
	return render(request, "core/login.html", {"form": form})


@login_required(login_url="login")
@require_GET
def shell(request: HttpRequest) -> HttpResponse:
	return render(request, "core/shell.html", {"identity": request.intevia_identity})


@login_required(login_url="login")
@require_GET
def restricted(request: HttpRequest) -> HttpResponse:
	identity = request.intevia_identity
	if identity.access_state != Identity.AccessState.RESTRICTED:
		return redirect(reverse("shell"))
	return render(request, "core/restricted.html", {"identity": identity})


@login_required(login_url="login")
@require_GET
def event_list(request: HttpRequest) -> HttpResponse:
	events = EventReadService.list_visible(request.intevia_identity)
	return render(request, "core/event_list.html", {"events": events})


@login_required(login_url="login")
@require_GET
def event_detail(request: HttpRequest, event_id: str) -> HttpResponse:
	try:
		event = EventReadService.inspect_event(request.intevia_identity, event_id)
	except EventNotVisible as exc:
		raise Http404("Event not found") from exc
	return render(request, "core/event_detail.html", {"event": event})


@login_required(login_url="login")
@require_GET
def registration_detail(
	request: HttpRequest,
	event_id: str,
	registration_id: str,
) -> HttpResponse:
	try:
		registration = EventReadService.inspect_registration(
			request.intevia_identity,
			event_id,
			registration_id,
		)
	except EventNotVisible as exc:
		raise Http404("Registration not found") from exc
	return render(
		request,
		"core/registration_detail.html",
		{"registration": registration, "event_id": event_id},
	)


@require_POST
def product_logout(request: HttpRequest) -> HttpResponse:
	logout(request)
	return redirect(reverse("login"))
