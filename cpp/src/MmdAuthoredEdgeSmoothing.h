#pragma once

#include <maya/MDagPath.h>
#include <maya/MFnMesh.h>
#include <maya/MFloatVectorArray.h>
#include <maya/MIntArray.h>
#include <maya/MItMeshEdge.h>
#include <maya/MVector.h>

#include <vector>

// Both native mesh builders restore PMX face-corner normals. Maya marks all
// edges hard during that operation, including edges with identical normals on
// both adjoining faces. Restore softness only where the authored normals are
// continuous at both endpoints; leave actual normal breaks and borders hard.
inline bool mmdSoftenContinuousAuthoredEdges(MFnMesh& meshFn, const MDagPath& meshPath)
{
    MIntArray faceCounts;
    MIntArray connects;
    MIntArray normalCounts;
    MIntArray normalIds;
    MFloatVectorArray normals;
    if (!meshFn.getVertices(faceCounts, connects) ||
        !meshFn.getNormals(normals, MSpace::kObject) ||
        !meshFn.getNormalIds(normalCounts, normalIds) ||
        faceCounts.length() != normalCounts.length() ||
        connects.length() != normalIds.length()) {
        return false;
    }
    std::vector<unsigned int> faceOffsets(faceCounts.length() + 1U, 0U);
    for (unsigned int face = 0; face < faceCounts.length(); ++face) {
        if (faceCounts[face] < 0 || faceCounts[face] != normalCounts[face]) {
            return false;
        }
        faceOffsets[face + 1U] = faceOffsets[face] + static_cast<unsigned int>(faceCounts[face]);
    }
    if (faceOffsets.back() != connects.length()) {
        return false;
    }
    const auto cornerNormal = [&](int face, int vertex, MVector& normal) -> bool {
        if (face < 0 || static_cast<unsigned int>(face) >= faceCounts.length()) {
            return false;
        }
        for (unsigned int corner = faceOffsets[face]; corner < faceOffsets[face + 1]; ++corner) {
            if (connects[corner] == vertex) {
                const int normalId = normalIds[corner];
                if (normalId < 0 || static_cast<unsigned int>(normalId) >= normals.length()) {
                    return false;
                }
                normal = MVector(normals[normalId]);
                return true;
            }
        }
        return false;
    };
    MStatus status;
    MItMeshEdge edgeIt(meshPath, MObject::kNullObj, &status);
    if (!status) {
        return false;
    }
    MIntArray smoothEdgeIds;
    MIntArray smoothValues;
    while (!edgeIt.isDone()) {
        MIntArray faces;
        if (edgeIt.getConnectedFaces(faces, &status) != 2 || !status) {
            if (!status) {
                return false;
            }
        } else {
            const int vertices[] = {edgeIt.index(0), edgeIt.index(1)};
            bool continuous = true;
            for (int vertex : vertices) {
                MVector left;
                MVector right;
                if (!cornerNormal(faces[0], vertex, left) ||
                    !cornerNormal(faces[1], vertex, right)) {
                    return false;
                }
                if ((left - right).length() > 1.0e-5) {
                    continuous = false;
                    break;
                }
            }
            if (continuous) {
                if (meshFn.isEdgeSmooth(edgeIt.index(), &status)) {
                    if (!status) {
                        return false;
                    }
                } else if (!status) {
                    return false;
                } else {
                    smoothEdgeIds.append(edgeIt.index());
                    smoothValues.append(1);
                }
            }
        }
        if (!edgeIt.next()) {
            return false;
        }
    }
    if (smoothEdgeIds.length() > 0 &&
        (!meshFn.setEdgeSmoothings(smoothEdgeIds, smoothValues) ||
         !meshFn.cleanupEdgeSmoothing() || !meshFn.updateSurface())) {
        return false;
    }
    return true;
}
