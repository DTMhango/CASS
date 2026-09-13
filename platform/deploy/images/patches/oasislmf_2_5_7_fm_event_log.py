"""Patch three defects in oasislmf 2.5.7's loss pipeline.

``FMReader.event_read_log`` builds a debug message out of
``sidx_indptr[next_compute_i]``, but ``next_compute_i`` counts computes and
``sidx_indptr`` is indexed by node. On any structure where the computes outrun
the nodes -- a portfolio of a few hundred items with more than one layer is
enough -- that raises ``IndexError`` and kills ``fmpy`` mid-stream. The loss
calculation that was running correctly dies with a kernel error, and every
insured and reinsurance run fails while ground-up, which never reaches this
code, succeeds.

The message is also built unconditionally: ``logger.debug(expr)`` evaluates
``expr`` whatever the log level, so lowering the engine's logging does not
avoid it.

Nothing here changes a number. The patched method emits the same message when
it is meaningful and skips it otherwise. It is applied when the worker image is
built, and it asserts the exact source it replaces, so an engine upgrade fails
the build rather than silently carrying a patch for a defect that has moved.

Upstream: oasislmf 2.5.7 is the latest release on PyPI as of 2026-09-13 and
still carries this. Remove this patch when a release fixes it.
"""

import pathlib
import sys

STREAM = "oasislmf/pytools/fm/stream_sparse.py"
STRUCTURE = "oasislmf/pytools/fm/financial_structure.py"
REINSURANCE = "oasislmf/preparation/reinsurance_layer.py"

ORIGINAL = """    def event_read_log(self, event_id):
        logger.debug(event_log_msg(event_id, self.sidx_indptr, self.len_array, self.compute_idx['next_compute_i']))
"""

PATCHED = """    def event_read_log(self, event_id):
        # CASS patch: the message indexes sidx_indptr, which is sized by node,
        # with a compute counter, and is built even when debug logging is off.
        if not logger.isEnabledFor(logging.DEBUG):
            return
        node_count = self.compute_idx['next_compute_i']
        if node_count <= 0 or node_count >= len(self.sidx_indptr):
            return
        logger.debug(event_log_msg(event_id, self.sidx_indptr, self.len_array, node_count))
"""


#: The second defect: the compute queue is sized with no headroom.
#:
#: ``computes`` holds every node visited while an event walks the financial
#: hierarchy, and it is allocated at exactly one slot per node plus one per
#: level. A structure that queues a node twice -- which a cross-layer node and
#: the output pass both do -- writes past the end of the array. Under numba
#: that is not an IndexError: it is a segmentation fault in the middle of a
#: loss calculation, with no indication of what went wrong.
#:
#: The queue is reset for each event (compute_sparse.py sets ``next_compute_i``
#: back to zero), so the requirement is bounded by the node count and this is a
#: sizing fix rather than a leak being papered over. Doubling the node span
#: costs four bytes a node -- megabytes on a national portfolio -- and changes
#: no arithmetic whatsoever: nothing reads the length except the allocation.
STRUCTURE_ORIGINAL = """    compute_len = node_level_start[-1] + steps + level_node_len[-1] + 1
"""

STRUCTURE_PATCHED = """    # CASS patch: headroom. A node can be queued more than once in an event,
    # and an exactly-sized queue overruns into a segmentation fault.
    compute_len = 2 * node_level_start[-1] + steps + level_node_len[-1] + 1
"""


#: The third defect: reinsurance output has no summary index to read.
#:
#: ``summarypy`` reads ``fmsummaryxref`` from each ``RI_n`` directory when it
#: summarises reinsurance losses, and nothing in the engine's file generation
#: ever writes one there. Every reinsurance analysis therefore builds its
#: reinsurance structures, starts the loss run, and stops on a missing file --
#: after the ground-up and insured streams have already been calculated.
#:
#: The index maps each output of the level to a summary. CASS asks for one
#: summary per perspective, the whole portfolio, which is exactly what the
#: engine writes for the insured level beside it: every output in summary 1 of
#: set 1. That is what this writes, and only where the engine wrote none. A
#: reinsurance analysis asking for any other summary level needs the upstream
#: fix rather than this patch, and CASS does not ask for one.
REINSURANCE_ORIGINAL = """            df_to_ndarray(fm_xref_df, fm_xref_dtype).tofile(os.path.join(ri_output_dir, "fm_xref.bin"))
"""

REINSURANCE_PATCHED = """            df_to_ndarray(fm_xref_df, fm_xref_dtype).tofile(os.path.join(ri_output_dir, "fm_xref.bin"))

            # CASS patch: the summary index the reinsurance loss stage reads,
            # which the engine otherwise never writes. Every output of this
            # level in summary 1 of set 1 -- the whole portfolio, matching the
            # insured level's own index.
            _cass_summary_xref = np.zeros(
                len(fm_xref_df),
                dtype=np.dtype([("output_id", "i4"), ("summary_id", "i4"), ("summaryset_id", "i4")]),
            )
            _cass_summary_xref["output_id"] = fm_xref_df["output"].to_numpy()
            _cass_summary_xref["summary_id"] = 1
            _cass_summary_xref["summaryset_id"] = 1
            _cass_summary_xref.tofile(os.path.join(ri_output_dir, "fmsummaryxref.bin"))
"""


def search_roots() -> list[pathlib.Path]:
    """Where the engine may be installed, whoever is running this."""
    roots = [pathlib.Path(path) for path in sys.path if path]
    roots += sorted(pathlib.Path("/").glob("home/*/.local/lib/python*/site-packages"))
    roots += sorted(pathlib.Path("/").glob("usr/local/lib/python*/site-packages"))
    return roots


def apply(root: pathlib.Path, relative: str, original: str, patched: str) -> bool:
    """Replace ``original`` with ``patched`` in one engine file."""
    target = root / relative
    if not target.is_file():
        return False
    source = target.read_text(encoding="utf-8")
    if patched in source:
        print(f"already patched: {target}")
        return True
    if original not in source:
        raise SystemExit(
            f"{target} does not carry the source this patch replaces. The engine "
            "version has moved: check whether the defect is fixed upstream and "
            "remove or rewrite this patch."
        )
    target.write_text(source.replace(original, patched, 1), encoding="utf-8")
    print(f"patched: {target}")
    return True


def main() -> int:
    wanted = [
        (STREAM, ORIGINAL, PATCHED),
        (STRUCTURE, STRUCTURE_ORIGINAL, STRUCTURE_PATCHED),
        (REINSURANCE, REINSURANCE_ORIGINAL, REINSURANCE_PATCHED),
    ]
    for relative, original, patched in wanted:
        for root in search_roots():
            if apply(root, relative, original, patched):
                break
        else:
            raise SystemExit(f"{relative} was not found on the import path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
