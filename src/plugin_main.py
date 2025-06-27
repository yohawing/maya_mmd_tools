import maya.cmds as cmds

def initializePlugin(mobject):
    '''
    Plugin entry point.
    '''
    vendor = "yohawing"
    version = "1.0.0"
    cmds.registerPlugin("maya_mmd_tools", vendor, version, initializePlugin, uninitializePlugin)
    print("maya_mmd_tools plugin loaded!")

def uninitializePlugin(mobject):
    '''
    Plugin exit point.
    '''
    cmds.deregisterPlugin("maya_mmd_tools")
    print("maya_mmd_tools plugin unloaded!")
