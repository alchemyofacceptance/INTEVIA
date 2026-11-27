"""Revise an admitted local Policy and declare authorship on its private draft receipt."""

from unicodedata import normalize
from urllib.parse import urlsplit
from uuid import UUID

from django.contrib.auth.decorators import login_required
from django.core import signing
from django.db import DatabaseError, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import Resolver404, resolve, reverse

from core.cache import personal_response
from core.discussion_forms import FORM_SALT, _envelope, help_line, issue_form
from core.law_pages import _unavailable, law_text_parts, policy_display_parts, split_policy_heading
from core.ui_views import is_prefetch
from src.intevia.services import discussion, library_laws, library_policy, policy_work


class FormInput(ValueError):
    """The reader can correct this entry on the open form."""


class Unconfirmed(Exception):
    """A write was attempted. Its exact receipt is the next place to check."""


def _record(destination, writer, **values):
    """Dispatch one confirmed command and redirect the browser to its natural address."""
    try:
        writer(**values)
    except DatabaseError as exc:
        raise Unconfirmed(destination) from exc
    response = HttpResponseRedirect(destination)
    response.status_code = 303
    return response


def _one(values, name):
    """Read exactly one browser value. Missing and ambiguous inputs have no effect."""
    items = values.getlist(name)
    return items[0] if len(items) == 1 else ""


def _post_review(request, operation, org, target, number):
    """Verify this session, user, operation, organisation and exact addressed version."""
    return _envelope(request, _one(request.POST, "review"), operation, org, target, number)


def _refuse(request, reason, back, *, declaration=False):
    """Use the shared five-part refusal card with an actual way back."""
    effect = "This request recorded no declaration." if declaration else "This request recorded no new version."
    facts = {
        "stale": (409, "The draft could not be saved.", "The Policy has a newer version than the one you reviewed.",
                  effect, "Review the Policy"),
        "authority": (403, "The draft could not be saved.", "Your role no longer allows you to revise this Policy.",
                      effect, "Back to Policy"),
        "conflict": (409, "The draft could not be saved.", "This form already recorded a different draft.",
                     effect, "Review the Policy"),
        "unknown": (503, "Your change could not be confirmed.", "The cause is unknown.",
                    "Your declaration may already be recorded." if declaration else "A new version may already be recorded.",
                    "Check the draft before trying again."),
        "read": (503, "The page could not be shown.", "The record could not be read just now.",
                 "This request saved nothing.", "Try opening the page again."),
        "csrf": (403, "The form could not be submitted.", "The form’s security check did not pass.",
                 effect, "Reopen the form."),
        "method": (405, "This request could not be used.", "This page accepts a visit or a submitted form.",
                   "This request saved nothing.", "Reopen the page."),
        "preview": (400, "The form could not be submitted.", "A preview cannot submit this form.",
                    effect, "Reopen the form."),
    }
    status, title, why, result, action = facts[reason]
    return render(request, "core/office_switch_refusal.html", dict(
        title=title, why=why, effect=result, action=action, next_url=back, help_text=help_line({})), status=status)


def csrf_refusal(request):
    """Provide the Policy form's way back when middleware refuses its security check."""
    return _refuse(request, "csrf", request.path, declaration=request.resolver_match.url_name == "policy_draft_receipt")


def _speculative(request):
    """Keep prefetch and prerender POSTs read-only."""
    return is_prefetch(request) or any("prerender" in request.headers.get(key, "").lower()
                                       for key in ("Purpose", "Sec-Purpose"))


def _payment(request, org, address):
    """Resolve a local result permalink without fetching a URL or trusting its visible wording."""
    if not address.strip():
        raise FormInput("Paste the link from the Board’s vote result page.")
    try:
        url = urlsplit(address.strip())
        if ((url.scheme or url.netloc) and (url.scheme not in ("http", "https")
                                             or url.netloc != request.get_host())):
            raise ValueError
        match = resolve(url.path)
        if match.url_name != "discussion_result" or match.kwargs["org"] != org:
            raise ValueError
        ids = match.kwargs
        result = policy_work.payment_citation(request.user, org, ids["circle"], ids["thread"], ids["result"])
    except (Resolver404, ValueError, discussion.DiscussionUnavailable):
        raise library_laws.LawReadUnavailable() from None
    return reverse(match.url_name, kwargs=ids), result


def _draft_fields(content, data=None):
    """Edit the wording of the released clauses, keeping their locators off the keyboard."""
    parts = [(key, value) for key, value in law_text_parts(content) if key != "payment-result"]
    fields = []
    for index, (key, value) in enumerate(parts or [(None, "")]):
        name = f"text_{index}"
        fields.append(dict(key=key, name=name, value=_one(data, name) if data is not None else value,
                           required=bool(value.strip()) or key is None))
    return fields


def _draft_content(fields, result, heading_line):
    """Serialize one server-chosen payment locator and preserve the reviewed clause identities."""
    lines = []
    for field in fields:
        value = field["value"].replace("\r\n", "\n").replace("\r", "\n")
        has_locator = any(key for key, _ in law_text_parts(normalize("NFC", value.strip())))
        if (field["required"] and not value.strip()) or has_locator or "\x00" in value:
            raise FormInput("Enter the Policy text in the labelled fields, without clause markers.")
        lines.append(f"[#{field['key']}] {value}" if field["key"] else value)
    if heading_line and not heading_line.endswith(("\r", "\n")):
        heading_line += "\n"
    return heading_line + "\n".join([*lines, f"[#payment-result] {result['post_uuid']}"])


def _author_keys(authors):
    """Return stable public identities for the complete reviewed roster."""
    return [str(row["identity_id"]) for row in authors]


def _revise(request, org, binding, number):
    """Admit the law first, then handle review, replay and a new revision under its lock."""
    back = reverse("organisation_law_version", args=(org, binding, number))
    post = request.method == "POST"
    token = _post_review(request, "revise_policy", org, binding, number) if post else None
    law, status = library_laws.our_law_version(credential=request.user, organism_uuid=org,
                                               binding=binding, version_number=number)
    access = policy_work.revision_access(request.user, org, law, status)
    if not access:
        return _unavailable(request)
    if not access["authorised"]:
        return _refuse(request, "authority", back) if token else _unavailable(request)
    if post and _speculative(request):
        return _refuse(request, "preview", request.path)
    reference = law["library_reference"]
    if not access["current"]:
        if not token or access["head"] == law["version"]:
            return _unavailable(request)
        # Only an existing receipt for this same request can cross a moved head.
        # draft_policy still compares its owner, actor, roster, source, predecessor and content.
        if (_one(request.POST, "action") != "save"
                or not policy_work.revision_was_received(UUID(token["request"]), law["version"])):
            return _refuse(request, "stale", back)
    carried = access["author_ids"]
    authors = policy_work.contributor_rows(set(carried) | {access["reader"]}, carried, access["reader"])
    snapshot = dict(predecessor=law["version"], authors=_author_keys(authors), reference=reference)
    if token and any(token["confirmation"].get(k) != v for k, v in snapshot.items()):
        return _unavailable(request)
    reviewed = token["confirmation"].get("payment", "") if token else ""
    data = request.POST if post else None
    heading_line, body = split_policy_heading(law["content"])
    fields = _draft_fields(body, data)
    context = dict(back=back, fields=fields, authors=authors, number=number, reference=reference,
                   catalogue_domains=True,
                   payment_input=(_one(data, "payment") if post else ""), help_text=help_line({}))
    form = _one(data, "review") if post else ""
    try:
        if post:
            action = _one(data, "action")
            if action == "change" and reviewed:
                context["payment_input"] = reviewed
                reviewed = ""
                form = ""
            else:
                if action == "review":
                    reviewed, result = _payment(request, org, context["payment_input"])
                    form = ""
                elif action == "save" and reviewed:
                    _, result = _payment(request, org, reviewed)
                else:
                    raise FormInput("Paste the vote result link and review the payment decision.")
                context.update(payment_url=reviewed, payment=result)
                if action == "save":
                    destination = reverse("policy_draft_receipt", args=(org, reference, number + 1))
                    return _record(destination, library_policy.draft_policy,
                        credential=request.user, organism_uuid=org, resource_reference=reference,
                        content=_draft_content(fields, result, heading_line),
                        authors=[UUID(value) for value in token["confirmation"]["authors"]],
                        request_uuid=UUID(token["request"]), predecessor_version_id=token["confirmation"]["predecessor"])
    except FormInput as exc:
        context["error"] = str(exc)
    if not form:
        confirmation = dict(snapshot, payment=reviewed)
        if token:
            # Reviewing or choosing another citation retains the request issued on GET.
            form = signing.dumps(dict(token, confirmation=confirmation), salt=FORM_SALT)
        else:
            form = issue_form(request, "revise_policy", org, binding, thread=number, confirmation=confirmation)
    context["review"] = form
    return render(request, "core/policy_revise.html", context, status=400 if context.get("error") else 200)


def _receipt(request, org, reference, version_number):
    """Bind the drafter’s affirmative response to the same bytes and complete roster they saw."""
    post = request.method == "POST"
    work = policy_work.draft_receipt(request.user, org, reference, version_number)
    target = dict(content_sha256=work["content_sha256"], authors=_author_keys(work["authors"]))
    back = (reverse("organisation_law_version", args=(org, work["binding"], work["version_number"]))
            if work["binding"] is not None else None)
    context = dict(work=work, back=back, catalogue_domains=True, help_text=help_line({}))
    if post:
        if not work["own"]:
            return _unavailable(request)
        token = _post_review(request, "declare_policy", org, reference, version_number)
        reviewed = token["confirmation"]
        if reviewed != target:
            return _unavailable(request)
        if _speculative(request):
            return _refuse(request, "preview", request.path, declaration=True)
        if _one(request.POST, "affirm") == "yes":
            return _record(request.path, library_policy.declare_authorship, credential=request.user, version_id=work["id"],
                content_sha256=reviewed["content_sha256"], authors=[UUID(value) for value in reviewed["authors"]],
                affirm=True)
        if not work["claim"]:
            context["error"] = "Confirm your authorship before submitting your declaration."
    if work["own"] and not work["claim"]:
        context["review"] = issue_form(request, "declare_policy", org, reference, thread=version_number,
                                       confirmation=target)
    context["parts"] = policy_display_parts(request.user, org, work["content"])
    return render(request, "core/policy_receipt.html", context, status=400 if context.get("error") else 200)


def _page(request, handler, org, target, number):
    """Finish the transaction before reporting a write, distinguishing a failed read from an uncertain save."""
    response = None
    declaring = handler is _receipt
    if request.method not in ("GET", "HEAD", "POST"):
        response = _refuse(request, "method", request.path)
        response["Allow"] = "GET, HEAD, POST"
        return response
    try:
        with transaction.atomic():
            response = handler(request, org, target, number)
        return response
    except (library_laws.LawReadUnavailable, discussion.DiscussionRefused):
        return _unavailable(request)
    except library_policy.PolicyRefused as exc:
        reason = {"LIB_STALE_HEAD": "stale", "LIB_AUTHORITY_SOURCE_REQUIRED": "authority",
                  "LIB_REQUEST_CONFLICT": "conflict"}.get(exc.code)
        if declaring or not reason:
            return _unavailable(request)
        return _refuse(request, reason, reverse("organisation_law_version", args=(org, target, number)))
    except Unconfirmed as exc:
        return _refuse(request, "unknown", exc.args[0], declaration=declaring)
    except DatabaseError:
        if response is not None and response.status_code == 303:
            # The service returned before the outer transaction could confirm its commit.
            return _refuse(request, "unknown", response["Location"], declaration=declaring)
        return _refuse(request, "read", request.path, declaration=declaring)


@personal_response
@login_required(login_url="login")
def revise(request, org, binding, version_number):
    """Issue or receive the signed form for an exact released local Policy."""
    return _page(request, _revise, org, binding, version_number)


@personal_response
@login_required(login_url="login")
def receipt(request, org, reference, version_number):
    """Read the private draft or record the drafter's explicit, exact-version declaration."""
    return _page(request, _receipt, org, reference, version_number)
