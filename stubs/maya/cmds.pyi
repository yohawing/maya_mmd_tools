# Maya Commands type definitions
# Auto-generated stub file for maya.cmds

from typing import Any, List, Optional, Union, Dict
from typing_extensions import Literal

# Common types
NodeName = str
AttributeName = str
MayaType = Union[str, int, float, bool, List[Any]]

# Node creation functions
def createNode(
    nodeType: str,
    name: Optional[str] = None,
    parent: Optional[str] = None,
    shared: bool = False,
    skipSelect: bool = False,
    **kwargs: Any
) -> str: ...

def polyCube(
    name: Optional[str] = None,
    constructionHistory: bool = True,
    createUVs: int = 2,
    width: float = 1.0,
    height: float = 1.0,
    depth: float = 1.0,
    subdivisionsX: int = 1,
    subdivisionsY: int = 1,
    subdivisionsZ: int = 1,
    **kwargs: Any
) -> List[str]: ...

def polySphere(
    name: Optional[str] = None,
    radius: float = 1.0,
    subdivisionsX: int = 20,
    subdivisionsY: int = 20,
    axis: Optional[List[float]] = None,
    constructionHistory: bool = True,
    createUVs: int = 2,
    **kwargs: Any
) -> List[str]: ...

def joint(
    name: Optional[str] = None,
    position: Optional[List[float]] = None,
    orientation: Optional[List[float]] = None,
    **kwargs: Any
) -> str: ...

# Attribute functions
def setAttr(
    attribute: str,
    *args: Any,
    type: Optional[str] = None,
    lock: bool = False,
    keyable: bool = True,
    channelBox: bool = False,
    **kwargs: Any
) -> None: ...

def getAttr(
    attribute: str,
    type: bool = False,
    size: bool = False,
    settable: bool = False,
    keyable: bool = False,
    channelBox: bool = False,
    multiIndices: bool = False,
    listChildren: bool = False,
    **kwargs: Any
) -> Any: ...

def addAttr(
    node: str,
    longName: Optional[str] = None,
    shortName: Optional[str] = None,
    attributeType: str = "double",
    defaultValue: Any = None,
    minValue: Optional[float] = None,
    maxValue: Optional[float] = None,
    keyable: bool = True,
    readable: bool = True,
    writable: bool = True,
    storable: bool = True,
    **kwargs: Any
) -> None: ...

def deleteAttr(node: str, attribute: str) -> None: ...

def connectAttr(
    sourceAttribute: str,
    destinationAttribute: str,
    force: bool = False,
    nextAvailable: bool = False,
    **kwargs: Any
) -> None: ...

def disconnectAttr(
    sourceAttribute: str,
    destinationAttribute: str,
    **kwargs: Any
) -> None: ...

def listConnections(
    node: str,
    source: bool = True,
    destination: bool = True,
    connections: bool = False,
    plugs: bool = False,
    **kwargs: Any
) -> Optional[List[str]]: ...

# Selection functions
def select(
    *args: Any,
    replace: bool = True,
    add: bool = False,
    deselect: bool = False,
    toggle: bool = False,
    all: bool = False,
    clear: bool = False,
    **kwargs: Any
) -> None: ...

def ls(
    *args: str,
    selection: bool = False,
    all: bool = False,
    type: Optional[str] = None,
    exactType: Optional[str] = None,
    long: bool = False,
    shortNames: bool = False,
    dagObjects: bool = False,
    **kwargs: Any
) -> List[str]: ...

def listRelatives(
    node: str,
    parent: bool = False,
    children: bool = False,
    shapes: bool = False,
    allDescendents: bool = False,
    fullPath: bool = False,
    type: Optional[str] = None,
    **kwargs: Any
) -> Optional[List[str]]: ...

# Transform functions
def xform(
    node: str,
    query: bool = False,
    edit: bool = False,
    matrix: bool = False,
    translation: Optional[List[float]] = None,
    rotation: Optional[List[float]] = None,
    scale: Optional[List[float]] = None,
    worldSpace: bool = False,
    objectSpace: bool = False,
    relative: bool = False,
    **kwargs: Any
) -> Any: ...

def move(
    x: float,
    y: float,
    z: float,
    *objects: str,
    relative: bool = False,
    worldSpace: bool = False,
    objectSpace: bool = False,
    **kwargs: Any
) -> None: ...

def rotate(
    x: float,
    y: float,
    z: float,
    *objects: str,
    relative: bool = False,
    worldSpace: bool = False,
    objectSpace: bool = False,
    **kwargs: Any
) -> None: ...

def scale(
    x: float,
    y: float,
    z: float,
    *objects: str,
    relative: bool = False,
    **kwargs: Any
) -> None: ...

# Group and hierarchy functions
def group(
    *objects: str,
    name: Optional[str] = None,
    parent: Optional[str] = None,
    world: bool = False,
    **kwargs: Any
) -> str: ...

def parent(
    *objects: str,
    world: bool = False,
    shape: bool = False,
    relative: bool = False,
    **kwargs: Any
) -> List[str]: ...

def ungroup(*objects: str, **kwargs: Any) -> List[str]: ...

# Delete functions
def delete(*objects: str, **kwargs: Any) -> None: ...

def duplicate(
    *objects: str,
    name: Optional[str] = None,
    parentOnly: bool = False,
    inputConnections: bool = False,
    **kwargs: Any
) -> List[str]: ...

# Material and shading functions
def shadingNode(
    nodeType: str,
    name: Optional[str] = None,
    asShader: bool = False,
    asTexture: bool = False,
    asLight: bool = False,
    asUtility: bool = False,
    **kwargs: Any
) -> str: ...

def sets(
    *objects: str,
    name: Optional[str] = None,
    forceElement: Optional[str] = None,
    **kwargs: Any
) -> Optional[str]: ...

def assignMaterial(material: str, *objects: str) -> None: ...

# File I/O functions
def file(
    query: bool = False,
    new: bool = False,
    open: bool = False,
    save: bool = False,
    import_: bool = False,
    exportAll: bool = False,
    exportSelected: bool = False,
    force: bool = False,
    type: Optional[str] = None,
    **kwargs: Any
) -> Optional[str]: ...

def loadPlugin(name: str, quiet: bool = False, **kwargs: Any) -> bool: ...
def unloadPlugin(name: str, force: bool = False, **kwargs: Any) -> None: ...

# Animation functions
def currentTime(query: bool = False, time: Optional[float] = None, **kwargs: Any) -> Optional[float]: ...

def setKeyframe(
    *objects: str,
    attribute: Optional[str] = None,
    time: Optional[float] = None,
    value: Optional[float] = None,
    **kwargs: Any
) -> None: ...

def keyframe(
    *objects: str,
    query: bool = False,
    edit: bool = False,
    time: Optional[tuple] = None,
    attribute: Optional[str] = None,
    **kwargs: Any
) -> Optional[List[float]]: ...

# Utility functions
def objExists(name: str) -> bool: ...
def nodeType(node: str, **kwargs: Any) -> str: ...
def rename(node: str, newName: str, **kwargs: Any) -> str: ...

def warning(message: str) -> None: ...
def error(message: str) -> None: ...

# Common attribute shortcuts
def getAttr(attr: str) -> Any: ...
def setAttr(attr: str, value: Any, **kwargs: Any) -> None: ...

# Mesh functions
def polyMergeVertex(
    *objects: str,
    distance: float = 0.001,
    alwaysMergeTwoVertices: bool = False,
    constructionHistory: bool = True,
    **kwargs: Any
) -> str: ...

def polyNormal(
    *objects: str,
    normalMode: int = 2,
    userNormalMode: bool = False,
    constructionHistory: bool = True,
    **kwargs: Any
) -> str: ...