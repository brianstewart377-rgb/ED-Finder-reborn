"""Fresh V3 Spansh importer vertical slice.

This package deliberately does not import or call the V2 ``import_spansh``
module.  The V3 adapter writes only unpublished generation relations and the
frozen V3 control-plane ledgers.
"""

from .adapter import CanonicalBatch, SpanshAdapter

__all__ = ["CanonicalBatch", "SpanshAdapter"]
