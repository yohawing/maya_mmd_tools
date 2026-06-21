/**
 * mmdRuntimeSmoke.cpp
 *
 * Standalone CLI-only C++ smoke executable for mmd-anim runtime verification.
 * Does NOT depend on Maya, mayapy, or GUI.
 *
 * Purpose:
 * - Read a GoldenOracle-style JSON manifest (deliberate small subset only).
 * - Resolve `assets.model` / `assets.motion` paths relative to the manifest file.
 * - For selected case(s): load PMX (required) + optional VMD via mmd::RuntimeBridge.
 * - Create model / (clip) / instance, evaluate at the case frames (default 0.0).
 * - Report per-case sanity: name, frame, bone count, morph count, world matrix float count,
 *   morph weight count, IK state count.
 * - Fail fast (exit non-zero) on: missing files, load/create/eval failures,
 *   empty world matrices, NaN/Inf in numeric outputs.
 * - No oracle JSONL comparison (v1 scope).
 *
 * Manifest subset supported (JSON, no 3rd-party parser):
 *   {
 *     "cases": [
 *       {
 *         "name": "case-identifier",
 *         "assets": { "model": "rel/path/to/model.pmx", "motion": "rel/path/to/motion.vmd?" },
 *         "frames": [0, 30, 60]
 *       },
 *       ...
 *     ]
 *   }
 * - "motion" is optional; if absent, frame evaluation is skipped after instance creation.
 * - Unknown fields are ignored. Both "frames": [...] and legacy "frame": 0 are accepted.
 * - Parser is intentionally minimal/isolated; only sufficient for the above shape.
 *
 * Build: produced by CMake as part of cpp/src (see CMakeLists.txt).
 * The ffi DLL is copied next to the exe by post-build step.
 *
 * Command line:
 *   mmd_runtime_smoke --manifest <path> [--case <name>] [--limit <n>]
 *
 * Nox entry: uvx nox -s cpp_cli_smoke -- --manifest <path> [--case <name>] [--limit <n>]
 * (cpp_build must have run for the matching --maya/--config so the exe exists.)
 *
 * Part of the cpp_verify chain (inserted before maya_smoke when --manifest supplied).
 */

#include "mmdRuntimeBridge.h"

#include <cctype>
#include <cmath>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using namespace mmd;

namespace {

/**
 * Read entire file as text (binary mode to preserve encoding; assume UTF-8 or ASCII for manifests).
 */
std::string readAllText(const fs::path& p) {
    std::ifstream ifs(p, std::ios::binary);
    if (!ifs) {
        return "";
    }
    std::ostringstream oss;
    oss << ifs.rdbuf();
    return oss.str();
}

/**
 * Tiny isolated manifest-subset parser (no external JSON library, stdlib only).
 * Deliberately limited to GoldenOracle-style case lists with name + assets.{model,motion} + optional frame.
 * Not a general JSON parser; do not extend without tests.
 */
std::string getJsonString(const std::string& src, const std::string& key) {
    std::string pat = "\"" + key + "\"";
    size_t k = src.find(pat);
    if (k == std::string::npos) return "";
    size_t colon = src.find(':', k + pat.size());
    if (colon == std::string::npos) return "";
    size_t q1 = colon + 1;
    while (q1 < src.size() && std::isspace(static_cast<unsigned char>(src[q1]))) ++q1;
    if (q1 >= src.size() || src[q1] != '"') return "";
    size_t q2 = src.find('"', q1 + 1);
    // skip escaped quotes (very basic)
    while (q2 != std::string::npos && q2 > 0 && src[q2 - 1] == '\\') {
        q2 = src.find('"', q2 + 1);
    }
    if (q2 == std::string::npos) return "";
    std::string val = src.substr(q1 + 1, q2 - q1 - 1);
    // minimal unescape for \"
    size_t esc = 0;
    while ((esc = val.find("\\\"", esc)) != std::string::npos) {
        val.replace(esc, 2, "\"");
        esc += 1;
    }
    esc = 0;
    while ((esc = val.find("\\\\", esc)) != std::string::npos) {
        val.replace(esc, 2, "\\");
        esc += 1;
    }
    esc = 0;
    while ((esc = val.find("\\/", esc)) != std::string::npos) {
        val.replace(esc, 2, "/");
        esc += 1;
    }
    return val;
}

float getJsonNumber(const std::string& src, const std::string& key, float defVal = 0.0f) {
    std::string pat = "\"" + key + "\"";
    size_t k = src.find(pat);
    if (k == std::string::npos) return defVal;
    size_t colon = src.find(':', k + pat.size());
    if (colon == std::string::npos) return defVal;
    size_t i = colon + 1;
    while (i < src.size() && std::isspace(static_cast<unsigned char>(src[i]))) ++i;
    if (i >= src.size()) return defVal;

    size_t start = i;
    if (i < src.size() && (src[i] == '-' || src[i] == '+')) ++i;
    bool hasDig = false;
    while (i < src.size() && std::isdigit(static_cast<unsigned char>(src[i]))) { hasDig = true; ++i; }
    if (i < src.size() && src[i] == '.') {
        ++i;
        while (i < src.size() && std::isdigit(static_cast<unsigned char>(src[i]))) { hasDig = true; ++i; }
    }
    if (i < src.size() && (src[i] == 'e' || src[i] == 'E')) {
        ++i;
        if (i < src.size() && (src[i] == '-' || src[i] == '+')) ++i;
        while (i < src.size() && std::isdigit(static_cast<unsigned char>(src[i]))) ++i;
    }
    if (!hasDig) return defVal;
    std::string num = src.substr(start, i - start);
    try {
        return std::stof(num);
    } catch (...) {
        return defVal;
    }
}

std::vector<float> getJsonNumberArray(const std::string& src, const std::string& key) {
    std::vector<float> out;
    std::string pat = "\"" + key + "\"";
    size_t k = src.find(pat);
    if (k == std::string::npos) return out;
    size_t colon = src.find(':', k + pat.size());
    if (colon == std::string::npos) return out;
    size_t open = src.find('[', colon + 1);
    if (open == std::string::npos) return out;

    bool inStr = false;
    size_t close = std::string::npos;
    for (size_t i = open + 1; i < src.size(); ++i) {
        char c = src[i];
        if (c == '"' && (i == 0 || src[i - 1] != '\\')) {
            inStr = !inStr;
        } else if (!inStr && c == ']') {
            close = i;
            break;
        }
    }
    if (close == std::string::npos) return out;

    std::string body = src.substr(open + 1, close - open - 1);
    size_t i = 0;
    while (i < body.size()) {
        while (i < body.size() && (std::isspace(static_cast<unsigned char>(body[i])) || body[i] == ',')) ++i;
        if (i >= body.size()) break;
        size_t start = i;
        if (body[i] == '-' || body[i] == '+') ++i;
        bool hasDig = false;
        while (i < body.size() && std::isdigit(static_cast<unsigned char>(body[i]))) { hasDig = true; ++i; }
        if (i < body.size() && body[i] == '.') {
            ++i;
            while (i < body.size() && std::isdigit(static_cast<unsigned char>(body[i]))) { hasDig = true; ++i; }
        }
        if (i < body.size() && (body[i] == 'e' || body[i] == 'E')) {
            ++i;
            if (i < body.size() && (body[i] == '-' || body[i] == '+')) ++i;
            while (i < body.size() && std::isdigit(static_cast<unsigned char>(body[i]))) ++i;
        }
        if (!hasDig) {
            ++i;
            continue;
        }
        try {
            out.push_back(std::stof(body.substr(start, i - start)));
        } catch (...) {
            // Ignore malformed entries in this manifest-subset parser.
        }
    }
    return out;
}

/**
 * Extract a JSON object substring starting at its '{' (balanced, string-aware).
 */
std::string getJsonObject(const std::string& src, const std::string& key) {
    std::string pat = "\"" + key + "\"";
    size_t k = src.find(pat);
    if (k == std::string::npos) return "";
    size_t colon = src.find(':', k + pat.size());
    if (colon == std::string::npos) return "";
    size_t obr = src.find('{', colon + 1);
    if (obr == std::string::npos) return "";
    int depth = 1;
    bool inStr = false;
    size_t i = obr + 1;
    for (; i < src.size() && depth > 0; ++i) {
        char c = src[i];
        if (c == '"') {
            if (i == 0 || src[i - 1] != '\\') inStr = !inStr;
        } else if (!inStr) {
            if (c == '{') ++depth;
            else if (c == '}') --depth;
        }
    }
    if (depth != 0) return "";
    return src.substr(obr, i - obr);
}

/**
 * Extract case object strings from the "cases" array (or root array fallback).
 * Uses brace counting; sufficient for the expected manifest shape.
 */
std::vector<std::string> extractCaseObjects(const std::string& json) {
    std::vector<std::string> objs;
    size_t arrStart = std::string::npos;

    size_t ck = json.find("\"cases\"");
    if (ck != std::string::npos) {
        size_t colon = json.find(':', ck);
        if (colon != std::string::npos) {
            arrStart = json.find('[', colon);
        }
    }
    if (arrStart == std::string::npos) {
        // fallback: root array of cases
        arrStart = json.find('[');
    }
    if (arrStart == std::string::npos) return objs;

    int depth = 0;
    bool inStr = false;
    size_t objStart = std::string::npos;
    for (size_t i = arrStart; i < json.size(); ++i) {
        char c = json[i];
        if (c == '"') {
            if (i == 0 || json[i - 1] != '\\') inStr = !inStr;
        }
        if (inStr) continue;
        if (c == '[' && i == arrStart) {
            depth = 1;
            continue;
        }
        if (c == '{') {
            if (depth == 1 && objStart == std::string::npos) objStart = i;
            ++depth;
        } else if (c == '}') {
            --depth;
            if (depth == 1 && objStart != std::string::npos) {
                objs.push_back(json.substr(objStart, i - objStart + 1));
                objStart = std::string::npos;
            }
        } else if (c == ']' && depth == 1) {
            break;
        }
    }
    return objs;
}

struct ManifestCase {
    std::string name;
    std::string model;   // relative to manifest
    std::string motion;  // relative, may be empty
    std::vector<float> frames;
};

std::vector<ManifestCase> parseManifest(const std::string& json) {
    std::vector<ManifestCase> cases;
    auto caseStrs = extractCaseObjects(json);
    for (const auto& s : caseStrs) {
        ManifestCase c;
        c.name = getJsonString(s, "name");
        if (c.name.empty()) continue;
        std::string assets = getJsonObject(s, "assets");
        c.model = getJsonString(assets, "model");
        c.motion = getJsonString(assets, "motion");
        c.frames = getJsonNumberArray(s, "frames");
        if (c.frames.empty()) {
            c.frames.push_back(getJsonNumber(s, "frame", 0.0f));
        }
        if (!c.model.empty()) {
            cases.push_back(c);
        }
    }
    return cases;
}

bool hasNanOrInf(const std::vector<float>& v) {
    for (float x : v) {
        if (std::isnan(x) || std::isinf(x)) return true;
    }
    return false;
}

fs::path resolveManifestPath(const fs::path& baseDir, const std::string& raw) {
    fs::path p = fs::u8path(raw);
    if (p.is_absolute()) {
        return p.lexically_normal();
    }
    return (baseDir / p).lexically_normal();
}

std::string pathToHostString(const fs::path& p) {
    return p.string();
}

void printUsage() {
    std::cout << "mmd_runtime_smoke -- standalone mmd-anim runtime sanity check\n"
              << "Usage:\n"
              << "  mmd_runtime_smoke --manifest <path.json> [--case <name>] [--limit <n>]\n"
              << "Notes:\n"
              << "  - Manifest is GoldenOracle-style subset (see file header).\n"
              << "  - Assets paths are resolved relative to the manifest's directory.\n"
              << "  - Evaluates frame 0 (or per-case 'frame') using RuntimeBridge.\n"
              << "  - Fails on missing assets, creation/eval errors, empty matrices, NaN/Inf.\n";
}

} // namespace

int main(int argc, char** argv) {
    std::string manifestPath;
    std::string caseFilter;
    int limit = 0;

    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if ((a == "--manifest" || a == "-m") && i + 1 < argc) {
            manifestPath = argv[++i];
        } else if ((a == "--case" || a == "-c") && i + 1 < argc) {
            caseFilter = argv[++i];
        } else if (a == "--limit" && i + 1 < argc) {
            try { limit = std::stoi(argv[++i]); } catch (...) { limit = 0; }
        } else if (a == "--help" || a == "-h") {
            printUsage();
            return 0;
        }
    }

    if (manifestPath.empty()) {
        std::cerr << "Error: --manifest <path> is required\n\n";
        printUsage();
        return 1;
    }

    fs::path manP(manifestPath);
    if (!fs::exists(manP)) {
        std::cerr << "Error: manifest not found: " << manP << "\n";
        return 1;
    }

    std::string jsonText = readAllText(manP);
    if (jsonText.empty()) {
        std::cerr << "Error: failed to read manifest or empty: " << manP << "\n";
        return 1;
    }

    auto allCases = parseManifest(jsonText);
    std::vector<ManifestCase> selected;
    for (const auto& c : allCases) {
        if (caseFilter.empty() || c.name == caseFilter) {
            selected.push_back(c);
        }
    }

    if (limit > 0 && static_cast<int>(selected.size()) > limit) {
        selected.resize(static_cast<size_t>(limit));
    }

    if (selected.empty()) {
        if (!caseFilter.empty()) {
            std::cerr << "Error: no case matched filter '" << caseFilter << "' (available cases: " << allCases.size() << ")\n";
            return 1;
        }
        std::cout << "No cases in manifest.\n";
        return 0;
    }

    fs::path baseDir = manP.parent_path();

    int exitCode = 0;
    for (const auto& cas : selected) {
        fs::path modelP = resolveManifestPath(baseDir, cas.model);
        fs::path motionP;
        if (!cas.motion.empty()) {
            motionP = resolveManifestPath(baseDir, cas.motion);
        }

        std::cout << "Case: " << cas.name << " frames=" << cas.frames.size() << std::endl;

        if (!fs::exists(modelP)) {
            std::cerr << "  Missing model file: " << modelP << "\n";
            return 1;
        }
        if (!motionP.empty() && !fs::exists(motionP)) {
            std::cerr << "  Missing motion file: " << motionP << "\n";
            return 1;
        }

        RuntimeBridge bridge;

        if (!bridge.createModelFromPmxFile(pathToHostString(modelP))) {
            std::cerr << "  Failed to create model from PMX: " << modelP << "\n";
            return 1;
        }

        bool hasClip = false;
        if (!motionP.empty()) {
            if (!bridge.createClipFromVmdFile(pathToHostString(motionP))) {
                std::cerr << "  Failed to create clip from VMD: " << motionP << "\n";
                return 1;
            }
            hasClip = true;
        }

        if (!bridge.createInstance()) {
            std::cerr << "  Failed to create runtime instance for model\n";
            return 1;
        }

        for (float fr : cas.frames) {
            if (hasClip) {
                if (!bridge.evaluateFrame(fr)) {
                    std::cerr << "  evaluateFrame(" << fr << ") failed\n";
                    return 1;
                }
            }

            auto world = bridge.getWorldMatrices();
            auto morph = bridge.getMorphWeights();
            auto ik = bridge.getIkEnabled();

            if (world.empty()) {
                std::cerr << "  Empty world matrix output (expected >= 16 floats for >=1 bone)\n";
                return 1;
            }

            if (hasNanOrInf(world) || hasNanOrInf(morph)) {
                std::cerr << "  NaN or Inf detected in world matrices or morph weights\n";
                return 1;
            }

            std::cout << "  OK frame=" << fr
                      << " bones=" << bridge.boneCount()
                      << " morphs=" << bridge.morphCount()
                      << " world_floats=" << world.size()
                      << " morph_weights=" << morph.size()
                      << " ik_states=" << ik.size()
                      << "\n";
        }
    }

    std::cout << "All selected cases passed (sanity only, no oracle compare).\n";
    return exitCode;
}
