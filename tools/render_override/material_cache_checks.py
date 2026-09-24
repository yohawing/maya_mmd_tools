"""Exercise material cache invalidation through live Maya connections and indices."""


def check_material_cache(cmds, proxies, capture, initial):
    """Compare cached rendering with explicit re-reads, then restore the scene."""
    connections = []
    for shape in proxies:
        for index in cmds.getAttr(shape + '.materialAlpha', multiIndices=True) or []:
            destination = f'{shape}.materialAlpha[{index}]'
            source = cmds.connectionInfo(destination, sourceFromDestination=True)
            if source:
                connections.append((source, destination))
    assert connections, 'fixture must have connected material alpha inputs'
    driver = cmds.createNode('addDoubleLinear')
    cmds.setAttr(driver + '.input1', 0.0)
    for source, destination in connections:
        cmds.disconnectAttr(source, destination)
        cmds.connectAttr(driver + '.output', destination)
    yield
    hidden = capture('cache_alpha_connected_zero')
    assert hidden['render'] != initial['render'], 'new alpha connection was not evaluated'
    cmds.setAttr(driver + '.input1', 1.0)
    yield
    opaque = capture('cache_alpha_driver_changed')
    assert opaque['render'] != hidden['render'], 'upstream alpha edit was not evaluated'
    for source, destination in connections:
        cmds.disconnectAttr(driver + '.output', destination)
        cmds.setAttr(destination, 0.0)
    yield
    assert capture('cache_alpha_disconnected_zero')['render'] == hidden['render']
    for source, destination in connections:
        cmds.connectAttr(source, destination)
    cmds.delete(driver)
    yield
    assert capture('cache_alpha_reconnected') == initial
    result = {'connections': len(connections), 'reindex': 'not_applicable_split_queue'}
    if len(proxies) == 1:
        # The native operation swaps queue indices without changing DG inputs.
        # A cached draw must agree with a subsequent explicit input re-read.
        shape = proxies[0]
        cmds.mmdRenderQueueReindex(node=shape, firstMaterialIndex=0, secondMaterialIndex=1)
        yield
        swapped = capture('cache_reindexed')
        source, destination = connections[0]
        cmds.disconnectAttr(source, destination)
        cmds.connectAttr(source, destination)
        yield
        assert capture('cache_reindexed_forced_read') == swapped, 'reindex retained stale material records'
        cmds.mmdRenderQueueReindex(node=shape, firstMaterialIndex=0, secondMaterialIndex=1)
        yield
        assert capture('cache_reindex_restored') == initial
        result['reindex'] = 'pass'
    return result
