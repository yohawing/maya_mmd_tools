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
 * static-count, and timed-MPlug-count.  The remaining values are laid out as
 * ``values[frame_index * channel_count + channel_index]``.
 */
class MmdVmdBatchSamplerCommand final : public MPxCommand {
public:
    static void* creator();
    static MSyntax newSyntax();

    MStatus doIt(const MArgList& args) override;
    bool isUndoable() const override;
};

