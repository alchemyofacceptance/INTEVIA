"""The owned test route's stages, told apart by database name (the INTEVIA Clock).

The owned route builds its databases in a fixed order, and each stage has its own name:

- bootstrap: test_intevia_boot_<tag>_<nonce>, where migrations run
  (verification/isolation/template.py);
- template:  test_intevia_tpl_(me|vr)_<tag>_<32 hex>, the bootstrap database renamed and sealed;
- control:   test_intevia_ctl_<tag>_<20 hex>, which checks the seal;
- clone:     test_intevia_living_organism_..., the attempt-owned database tests run on
  (the pattern the lifecycle backend already enforces, S015_DATABASE_PATTERN).

Any other name belongs to no stage. A production-shaped name, and a name that only
resembles a stage's, are refused alike.
"""

import re

from .operations import S015_DATABASE_PATTERN

BOOTSTRAP = "bootstrap"
TEMPLATE = "template"
CONTROL = "control"
CLONE = "clone"

_STAGES = (
    (BOOTSTRAP, re.compile(r"test_intevia_boot_[a-z0-9]{1,8}_[0-9a-f]{32}")),
    (TEMPLATE, re.compile(r"test_intevia_tpl_(?:me|vr)_[a-z0-9]{1,8}_[0-9a-f]{32}")),
    (CONTROL, re.compile(r"test_intevia_ctl_[a-z0-9]{1,8}_[0-9a-f]{20}")),
    (CLONE, S015_DATABASE_PATTERN),
)


def owned_stage(name):
    """Return the owned route's stage this database name belongs to, or None."""
    if not isinstance(name, str):
        return None
    for stage, pattern in _STAGES:
        if pattern.fullmatch(name):
            return stage
    return None
