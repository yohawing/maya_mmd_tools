# -*- coding: utf-8 -*-

import os
import maya.api.OpenMaya as om
import maya.api.OpenMayaUI as omui
import maya.api.OpenMayaRender as omr

# Shader node name
SHADER_NODE_NAME = "MMDShader"
# Shader file path (relative to the plugin root)
SHADER_FX_FILE = "shaders/MMDShader.fx"


def maya_useNewAPI():
    """The presence of this function tells Maya that the plugin produces, and
    expects to be passed, objects created using the Maya Python API 2.0.
    """
    pass


# ----------------------------------------------------------------------
# Shader Node Implementation
# ----------------------------------------------------------------------
class MMDShaderNode(om.MPxNode):
    """Custom shader node definition."""

    kNodeName = SHADER_NODE_NAME
    kNodeId = om.MTypeId(0x0007F7F7)  # Unique ID, must be registered
    drawDbClassification = f"drawdb/shader/surface/{kNodeName}"
    classification = "shader/surface"  # For Hypershade

    def __init__(self):
        super(MMDShaderNode, self).__init__()

    @classmethod
    def creator(cls):
        return cls()

    def compute(self, plug, dataBlock):
        """Compute the output color."""
        if plug == self.a_out_color:
            # Get input values
            diffuse_handle = dataBlock.inputValue(self.a_diffuse_color)
            diffuse_color = diffuse_handle.asFloat3()

            # Get main texture if connected
            texture_handle = dataBlock.inputValue(self.a_main_texture)
            texture_color = texture_handle.asFloat3()

            # Multiply diffuse color with texture color
            final_color = [
                diffuse_color[0] * texture_color[0],
                diffuse_color[1] * texture_color[1],
                diffuse_color[2] * texture_color[2],
            ]

            # Set output color
            out_handle = dataBlock.outputValue(self.a_out_color)
            out_handle.set3Float(final_color[0], final_color[1], final_color[2])
            dataBlock.setClean(plug)

    @classmethod
    def initialize(cls):
        """Initialize node attributes."""
        om.MGlobal.displayInfo("MMDShaderNode: Starting initialization")
        n_attr = om.MFnNumericAttribute()

        # Diffuse Color
        cls.a_diffuse_color = n_attr.create(
            "diffuseColor", "dc", om.MFnNumericData.k3Float
        )
        n_attr.usedAsColor = True
        n_attr.default = (0.8, 0.8, 0.8)
        n_attr.keyable = True
        cls.addAttribute(cls.a_diffuse_color)

        # Shininess
        cls.a_shininess = n_attr.create(
            "shininess", "sh", om.MFnNumericData.kFloat, 1.0
        )
        n_attr.setMin(0.0)
        n_attr.keyable = True
        cls.addAttribute(cls.a_shininess)

        # Specular Color
        cls.a_specular_color = n_attr.create(
            "specularColor", "sc", om.MFnNumericData.k3Float
        )
        n_attr.usedAsColor = True
        n_attr.default = (0.5, 0.5, 0.5)
        n_attr.keyable = True
        cls.addAttribute(cls.a_specular_color)

        # Ambient Color
        cls.a_ambient_color = n_attr.create(
            "ambientColor", "ac", om.MFnNumericData.k3Float
        )
        n_attr.usedAsColor = True
        n_attr.default = (0.3, 0.3, 0.3)
        n_attr.keyable = True
        cls.addAttribute(cls.a_ambient_color)

        # Edge Color
        cls.a_edge_color = n_attr.create("edgeColor", "ec", om.MFnNumericData.k3Float)
        n_attr.usedAsColor = True
        n_attr.default = (0.0, 0.0, 0.0)
        n_attr.keyable = True
        cls.addAttribute(cls.a_edge_color)

        # Edge Size
        cls.a_edge_size = n_attr.create(
            "edgeSize", "es", om.MFnNumericData.kFloat, 0.01
        )
        n_attr.setMin(0.0)
        n_attr.keyable = True
        cls.addAttribute(cls.a_edge_size)

        # Sphere Mode
        e_attr = om.MFnEnumAttribute()
        cls.a_sphere_mode = e_attr.create("sphereMode", "sm", 0)
        e_attr.addField("None", 0)
        e_attr.addField("Multiply", 1)
        e_attr.addField("Add", 2)
        e_attr.keyable = True
        cls.addAttribute(cls.a_sphere_mode)

        # Texture Attributes (color inputs for texture connections)
        # These can accept file texture nodes or any other color output
        cls.a_main_texture = n_attr.create(
            "mainTexture", "mt", om.MFnNumericData.k3Float
        )
        n_attr.usedAsColor = True
        n_attr.default = (1.0, 1.0, 1.0)  # Default white
        n_attr.keyable = False
        n_attr.connectable = True
        cls.addAttribute(cls.a_main_texture)

        cls.a_sphere_texture = n_attr.create(
            "sphereTexture", "st", om.MFnNumericData.k3Float
        )
        n_attr.usedAsColor = True
        n_attr.default = (1.0, 1.0, 1.0)  # Default white
        n_attr.keyable = False
        n_attr.connectable = True
        cls.addAttribute(cls.a_sphere_texture)

        cls.a_toon_texture = n_attr.create(
            "toonTexture", "tt", om.MFnNumericData.k3Float
        )
        n_attr.usedAsColor = True
        n_attr.default = (1.0, 1.0, 1.0)  # Default white
        n_attr.keyable = False
        n_attr.connectable = True
        cls.addAttribute(cls.a_toon_texture)

        # Output color attribute (required for shader nodes)
        out_attr = om.MFnNumericAttribute()
        cls.a_out_color = out_attr.create("outColor", "oc", om.MFnNumericData.k3Float)
        out_attr.usedAsColor = True
        out_attr.writable = False
        out_attr.readable = True
        out_attr.storable = True
        cls.addAttribute(cls.a_out_color)

        # Set attribute affects for proper updates
        cls.attributeAffects(cls.a_diffuse_color, cls.a_out_color)
        cls.attributeAffects(cls.a_specular_color, cls.a_out_color)
        cls.attributeAffects(cls.a_ambient_color, cls.a_out_color)
        cls.attributeAffects(cls.a_edge_color, cls.a_out_color)
        cls.attributeAffects(cls.a_shininess, cls.a_out_color)
        cls.attributeAffects(cls.a_edge_size, cls.a_out_color)
        cls.attributeAffects(cls.a_sphere_mode, cls.a_out_color)
        cls.attributeAffects(cls.a_main_texture, cls.a_out_color)
        cls.attributeAffects(cls.a_sphere_texture, cls.a_out_color)
        cls.attributeAffects(cls.a_toon_texture, cls.a_out_color)

        om.MGlobal.displayInfo("MMDShaderNode: Initialization completed")


# ----------------------------------------------------------------------
# Shader Override Implementation
# ----------------------------------------------------------------------
class MMDShaderOverride(omr.MPxShaderOverride):
    """The shader override responsible for drawing the MMD shader."""

    def __init__(self, obj):
        """Initialize the shader override."""
        super(MMDShaderOverride, self).__init__(obj)

        self.shader = None
        self.shader_path = os.path.join(os.path.dirname(__file__), "..", SHADER_FX_FILE)
        om.MGlobal.displayInfo(
            f"MMDShaderOverride: Loading shader from {self.shader_path}"
        )

        # Cached values for material properties
        self.diffuse_color = om.MColor((0.8, 0.8, 0.8))
        self.shininess = 1.0
        self.specular_color = om.MColor((0.5, 0.5, 0.5))
        self.ambient_color = om.MColor((0.3, 0.3, 0.3))
        self.edge_color = om.MColor((0.0, 0.0, 0.0))
        self.edge_size = 0.01
        self.sphere_mode = 0

    @classmethod
    def creator(cls, obj):
        """Plugin creation entry point."""
        return cls(obj)

    def supportedDrawAPIs(self):
        """Declare support for multiple rendering APIs."""
        return (
            omr.MRenderer.kDirectX11
            | omr.MRenderer.kOpenGL
            | omr.MRenderer.kOpenGLCoreProfile
        )

    def initialize(self, initContext, userdata):
        """Called once to initialize the shader instance."""
        om.MGlobal.displayInfo("MMDShaderOverride: initialize() called")
        shader_mgr = omr.MRenderer.getShaderManager()
        if not shader_mgr:
            om.MGlobal.displayError("MMDShaderOverride: Failed to get shader manager")
            return

        # Load the .fx shader file
        om.MGlobal.displayInfo(
            f"MMDShaderOverride: Loading shader from {self.shader_path}"
        )
        self.shader = shader_mgr.getEffectsFileShader(self.shader_path, "MMDTechnique")
        if not self.shader:
            om.MGlobal.displayError(f"Failed to load shader file: {self.shader_path}")
            return
        om.MGlobal.displayInfo("MMDShaderOverride: Shader loaded successfully")

        # Create rasterizer states for fill and outline
        raster_state_desc = omr.MRasterizerStateDesc()
        raster_state_desc.cullMode = omr.MRasterizerState.kCullFront
        self.outline_raster_state = omr.MStateManager.acquireRasterizerState(
            raster_state_desc
        )

        raster_state_desc.cullMode = omr.MRasterizerState.kCullBack
        self.fill_raster_state = omr.MStateManager.acquireRasterizerState(
            raster_state_desc
        )

    def updateDG(self, obj):
        """Called when node attributes change."""
        om.MGlobal.displayInfo("MMDShaderOverride: updateDG() called")
        if not obj or obj.isNull():
            return

        node = om.MFnDependencyNode(obj)

        # Get MColor objects directly from the plugs
        self.diffuse_color = om.MColor(
            node.findPlug("diffuseColor", False).asMDataHandle().asFloat3()
        )
        self.specular_color = om.MColor(
            node.findPlug("specularColor", False).asMDataHandle().asFloat3()
        )
        self.ambient_color = om.MColor(
            node.findPlug("ambientColor", False).asMDataHandle().asFloat3()
        )
        self.edge_color = om.MColor(
            node.findPlug("edgeColor", False).asMDataHandle().asFloat3()
        )

        # Get other numeric values
        self.shininess = node.findPlug("shininess", False).asFloat()
        self.edge_size = node.findPlug("edgeSize", False).asFloat()
        self.sphere_mode = node.findPlug("sphereMode", False).asInt()

    def updateDevice(self):
        """Called when the render device changes."""
        # ... (device-specific update logic will go here)
        pass

    def endUpdate(self):
        """Called at the end of the update phase for cleanup."""
        pass

    def handlesDraw(self, context):
        """Returns True if shader handles drawing for the given context."""
        # Handle drawing for color passes
        return context.getPassContext().passIdentifier() == omr.MPassContext.kColorPassName

    def handlesConsolidatedGeometry(self):
        """Returns True if the shader can handle consolidated geometry."""
        # Allow consolidated geometry for better performance
        return True

    def activateKey(self, context, key):
        """Called to activate the shader for drawing."""
        om.MGlobal.displayInfo("MMDShaderOverride: activateKey() called")
        # Return true to indicate we want to handle the drawing
        return True

    def terminateKey(self, context, key):
        """Called to terminate the shader after drawing."""
        pass

    def terminate(self):
        """Called to terminate the shader."""
        if self.shader:
            shader_mgr = omr.MRenderer.getShaderManager()
            if shader_mgr:
                shader_mgr.releaseShader(self.shader)
            self.shader = None

    def draw(self, context, renderables):
        """The main drawing callback."""
        om.MGlobal.displayInfo(
            f"MMDShaderOverride: draw() called with {len(renderables)} renderables"
        )
        if not self.shader:
            om.MGlobal.displayWarning(
                "MMDShaderOverride: No shader loaded, skipping draw"
            )
            return

        # --- Draw each renderable item ---
        for item in renderables:
            # --- Pass 1: Edge ---
            self.shader.setParameter(
                "EdgeColor",
                (self.edge_color.r, self.edge_color.g, self.edge_color.b, 1.0),
            )
            self.shader.setParameter("EdgeSize", self.edge_size)
            self.shader.activatePass(context, 0)  # Activate EdgePass
            omr.MPxShaderOverride.drawGeometry(context, item)

            # --- Pass 2: Main ---
            self.shader.setParameter(
                "DiffuseColor",
                (self.diffuse_color.r, self.diffuse_color.g, self.diffuse_color.b, 1.0),
            )
            self.shader.setParameter(
                "SpecularColor",
                (self.specular_color.r, self.specular_color.g, self.specular_color.b),
            )
            self.shader.setParameter("Shininess", self.shininess)
            self.shader.setParameter(
                "AmbientColor",
                (self.ambient_color.r, self.ambient_color.g, self.ambient_color.b),
            )
            self.shader.setParameter("SphereMode", self.sphere_mode)
            self.shader.activatePass(context, 1)  # Activate MainPass
            omr.MPxShaderOverride.drawGeometry(context, item)


# ----------------------------------------------------------------------
# Plugin Registration
# ----------------------------------------------------------------------


def initializePlugin(plugin):
    """Register the shader node and override."""
    vendor = "yohawing"
    version = "1.0.0"
    plugin_fn = om.MFnPlugin(plugin, vendor, version)

    try:
        plugin_fn.registerNode(
            SHADER_NODE_NAME,
            MMDShaderNode.kNodeId,
            MMDShaderNode.creator,
            MMDShaderNode.initialize,
            om.MPxNode.kDependNode,
            MMDShaderNode.classification,
        )
    except:
        om.MGlobal.displayError(f"Failed to register node: {SHADER_NODE_NAME}")
        raise

    # Register shader override for viewport drawing
    try:
        omr.MDrawRegistry.registerShaderOverrideCreator(
            MMDShaderNode.drawDbClassification,
            "mmdShaderOverride",  # Unique registrant ID
            MMDShaderOverride.creator,
        )
    except:
        om.MGlobal.displayError("Failed to register shader override.")
        raise


def uninitializePlugin(plugin):
    """Deregister the shader node and override."""
    plugin_fn = om.MFnPlugin(plugin)

    try:
        omr.MDrawRegistry.deregisterShaderOverrideCreator(
            MMDShaderNode.drawDbClassification, "mmdShaderOverride"
        )
    except:
        om.MGlobal.displayError("Failed to deregister shader override.")
        raise

    try:
        plugin_fn.deregisterNode(MMDShaderNode.kNodeId)
    except:
        om.MGlobal.displayError(f"Failed to deregister node: {SHADER_NODE_NAME}")
        raise
