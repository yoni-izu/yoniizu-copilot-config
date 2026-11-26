"""Legacy name of :mod:`jobs_view` (kept until the legacy job layouts retire)."""
import sys

import jobs_view

sys.modules[__name__] = jobs_view
