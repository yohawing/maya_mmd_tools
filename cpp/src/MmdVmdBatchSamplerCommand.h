#pragma once

#include <maya/MArgList.h>
#include <maya/MPxCommand.h>
#include <maya/MSyntax.h>

/**
 * Read-only, frame-major scalar sampler used by the Bake Timeline prepare path.
 *
 * The command accepts a versioned JSON request through ``-payload`` and
 * returns one MDoubleArray.  The first six doubles are the fixed protocol
 * header: version, frame count, channel count, direct-curve count,
 * static-count, and timed-MPlug-count.  A direct-spool request carrying
 * ``timing=wall_v3`` adds timing values and compound classification/runtime
 * diagnostics after that header, followed by a fixed acknowledgement version,
 * checkpoint count, and one ten-value record per 120-frame checkpoint.
 * A ``mode=direct_spool`` request receives the complete Prepare frame plan
 * once, resolves/classifies its plugs once, and writes frame-major doubles to
 * the pre-sized Python-owned spool path.  It retains 120-frame internal
 * checkpoints and returns only the fixed header/diagnostics.
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
