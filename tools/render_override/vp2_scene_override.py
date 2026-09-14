"""Shared VP2 scene-pass override used by bounded capability checks."""


OVERRIDE_NAME = "vp2Feasibility"


def make_override():
    """Build a scene-only override following Autodesk's object-set example."""
    from maya.api import OpenMaya as om
    from maya.api import OpenMayaRender as omr

    class Scene(omr.MSceneRender):
        def __init__(self, name, members, first, shader=None):
            super().__init__(name)
            self.members = None
            if members is not None:
                self.members = om.MSelectionList()
                for member in members:
                    self.members.add(member)
            self.first = first
            self.shader = shader

        def shaderOverride(self):
            return self.shader

        def objectSetOverride(self):
            return self.members

        def renderFilterOverride(self):
            return omr.MSceneRender.kRenderShadedItems

        def clearOperation(self):
            self.mClearOperation.setMask(
                omr.MClearOperation.kClearAll if self.first else omr.MClearOperation.kClearNone
            )
            self.mClearOperation.setClearGradient(False)
            self.mClearOperation.setClearColor((0.0, 0.0, 0.0, 1.0))
            return self.mClearOperation

    class Override(omr.MRenderOverride):
        def __init__(self):
            super().__init__(OVERRIDE_NAME)
            self.operations = []
            self.index = 0
            self.shadow_requests = []

        def request_shadows(self, lights):
            """Queue native maps before scene execution, as in Autodesk's sample."""
            selection = om.MSelectionList()
            for light in lights:
                selection.add(light)
            self.shadow_requests = [selection.getDependNode(i) for i in range(selection.length())]

        def setup(self, destination):
            for light in self.shadow_requests:
                omr.MRenderer.setLightRequiresShadows(light, True)

        def configure(self, groups, shaders=None):
            shaders = shaders or [None] * len(groups)
            self.operations = [
                Scene("probeScene%d" % i, members, i == 0, shaders[i])
                for i, members in enumerate(groups)
            ]
            self.operations.append(omr.MPresentTarget("probePresent"))

        def supportedDrawAPIs(self):
            return omr.MRenderer.kDirectX11

        def startOperationIterator(self):
            self.index = 0
            return True

        def renderOperation(self):
            return self.operations[self.index]

        def nextRenderOperation(self):
            self.index += 1
            return self.index < len(self.operations)

        def uiName(self):
            return "VP2 feasibility probe"

    return Override()
