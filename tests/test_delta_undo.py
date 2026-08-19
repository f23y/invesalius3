import os

import numpy as np

from invesalius.data.mask import DeltaHistoryNode, EditionHistory


def test_delta_history_node_sparse():
    # Create mock 3D matrix (e.g. 50x50x50)
    p_matrix = np.zeros((50, 50, 50), dtype=np.uint8)
    p_matrix[10:20, 10:20, 10:20] = 255

    # New matrix with a stroke applied (modifying 5x5x5 voxels)
    new_matrix = p_matrix.copy()
    new_matrix[12:17, 12:17, 12:17] = 0

    # Instantiate DeltaHistoryNode
    node = DeltaHistoryNode(0, "VOLUME", p_matrix, new_matrix)

    # Verify only modified voxels are stored
    num_changed = 5 * 5 * 5
    assert len(node.indices[0]) == num_changed
    assert len(node.old_values) == num_changed
    assert len(node.new_values) == num_changed

    # Test undo application on new_matrix
    test_matrix = new_matrix.copy()
    node.apply_undo(test_matrix)
    assert np.array_equal(test_matrix, p_matrix)

    # Test redo application on p_matrix
    node.apply_redo(test_matrix)
    assert np.array_equal(test_matrix, new_matrix)


def test_delta_history_node_serialization():
    p_matrix = np.zeros((30, 30, 30), dtype=np.uint8)
    new_matrix = p_matrix.copy()
    new_matrix[5:10, 5:10, 5:10] = 255

    node = DeltaHistoryNode(0, "VOLUME", p_matrix, new_matrix)

    # Serialize to disk
    node.serialize_to_disk()
    assert node.filename is not None
    assert os.path.exists(node.filename)
    assert node.indices is None  # Arrays cleared from RAM

    # Test in-memory restoration and undo/redo
    test_matrix = p_matrix.copy()
    node.apply_redo(test_matrix)
    assert np.array_equal(test_matrix, new_matrix)


def test_edition_history_volume_deltas():
    history = EditionHistory(size=10)
    matrix = np.zeros((40, 40, 40), dtype=np.uint8)

    # State 0 -> State 1
    orig_1 = matrix.copy()
    matrix[10:15, 10:15, 10:15] = 255
    history.new_node(0, "VOLUME", matrix.copy(), orig_1, clean=False)

    # State 1 -> State 2
    orig_2 = matrix.copy()
    matrix[12:18, 12:18, 12:18] = 0
    history.new_node(0, "VOLUME", matrix.copy(), orig_2, clean=False)

    assert len(history.history) == 2
    assert history.index == 1

    # Undo Stroke 2 -> should return to State 1
    history.undo(matrix)
    assert np.array_equal(matrix, orig_2)
    assert history.index == 0

    # Undo Stroke 1 -> should return to State 0
    history.undo(matrix)
    assert np.array_equal(matrix, orig_1)
    assert history.index == -1

    # Redo Stroke 1 -> should return to State 1
    history.redo(matrix)
    assert np.array_equal(matrix, orig_2)
    assert history.index == 0
