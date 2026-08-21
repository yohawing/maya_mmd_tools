#pragma once

#include <maya/MArgList.h>
#include <maya/MPxCommand.h>
#include <maya/MSyntax.h>

/**
 * Read-only, frame-major scalar sampler used by the Mode C prepare path.
 *
 * The command accepts a versioned JSON request through ``-payload`` and
 * returns one MDoubleArray.  The first six doubles are the fixed protocol
 * header: version, frame count, channel count, direct-curve count,
 * static-count, and timed-MPlug-count.  A request carrying ``timing=wall_v1``
 * adds three timing values after that header (set-current-time wall seconds,
 * first-timed-MPlug-read wall seconds, and complete-channel-loop wall
 * seconds), followed by the frame-major values.  ``timing=wall_v2`` adds two
 * exact non-negative integer classification diagnostics after those timings:
 * compound-group count and covered-channel count.  ``timing=wall_v3`` keeps
 * those fields and adds successful/fallback group and covered-channel counts;
 * a group that falls back remains on scalar reads for subsequent frames.
 * ``wall_v1`` and ``wall_v2`` remain available for compatibility.
 * A ``mode=direct_spool`` request receives the complete Prepare frame plan
 * once, resolves/classifies its plugs once, and writes frame-major doubles to
 * the pre-sized Python-owned spool path.  It retains 120-frame internal
 * checkpoints and returns only the fixed header/diagnostics, leaving the old
 * packed protocol available as an oracle/fallback.
 * The request must explicitly carry the ``maya_timeline_bake_v1`` evaluation
 * policy; there is no alternate DG-context production path.
 */
class MmdVmdBatchSamplerCommand final : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();

    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override;
};
