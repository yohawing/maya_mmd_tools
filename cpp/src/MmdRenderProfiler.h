#pragma once

#include <maya/MProfiler.h>

// Keep the category reusable across plugin reloads. Maya owns recorded events;
// scopes report inclusive CPU time, including any waits in Maya/DX11 calls.
inline int mmdRenderProfileCategory()
{
    static const int category = [] {
        const int existing = MProfiler::getCategoryIndex("MMD Render");
        return existing >= 0 ? existing : MProfiler::addCategory(
            "MMD Render", "MMD evaluated geometry and ordered DX11 CPU work");
    }();
    return category;
}
