import maya.cmds as cmds

def initializePlugin(mobject):
    '''
    Plugin entry point.
    '''
    vendor = "Your Name/Company"
    version = "1.0.0"
    cmds.registerPlugin("mmd_importer", vendor, version, initializePlugin, uninitializePlugin)
    print("mmd_importer plugin loaded!")

def uninitializePlugin(mobject):
    '''
    Plugin exit point.
    '''
    cmds.deregisterPlugin("mmd_importer")
    print("mmd_importer plugin unloaded!")
