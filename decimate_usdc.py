"""Decimate the visual meshes of a USD mesh layer, preserving material subsets and hard edges."""

import argparse

import numpy as np
import open3d as o3d
from pxr import Usd, UsdGeom, Vt
from scipy.spatial import cKDTree


def crease_normals(points: np.ndarray, faces: np.ndarray, crease_deg: float) -> np.ndarray:
    """Face-varying normals: average area-weighted normals of faces sharing a vertex within
    the crease angle, so smooth surfaces shade smoothly and CAD hard edges stay sharp."""
    e1 = points[faces[:, 1]] - points[faces[:, 0]]
    e2 = points[faces[:, 2]] - points[faces[:, 0]]
    cross = np.cross(e1, e2)
    area = np.linalg.norm(cross, axis=1, keepdims=True)
    unit = cross / np.maximum(area, 1e-20)

    corner_vertex = faces.ravel()
    corner_face = np.repeat(np.arange(len(faces)), 3)
    order = np.argsort(corner_vertex, kind="stable")
    _, starts, sizes = np.unique(corner_vertex[order], return_index=True, return_counts=True)
    pair_counts = sizes.astype(np.int64) ** 2
    group = np.repeat(np.arange(len(sizes)), pair_counts)
    local = np.arange(pair_counts.sum()) - np.repeat(np.cumsum(pair_counts) - pair_counts, pair_counts)
    a = order[starts[group] + local // sizes[group]]
    b = order[starts[group] + local % sizes[group]]
    fa, fb = corner_face[a], corner_face[b]
    keep = np.einsum("ij,ij->i", unit[fa], unit[fb]) >= np.cos(np.deg2rad(crease_deg))
    normals = np.zeros((len(corner_vertex), 3))
    np.add.at(normals, a[keep], cross[fb[keep]])
    norm = np.linalg.norm(normals, axis=1, keepdims=True)
    fallback = unit[corner_face]
    return np.where(norm > 1e-20, normals / np.maximum(norm, 1e-20), fallback)


def decimate_mesh(mesh: UsdGeom.Mesh, min_faces: int, ratio: float, crease_deg: float) -> tuple[int, int]:
    counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get())
    num_faces = len(counts)
    max_faces = max(min_faces, int(ratio * num_faces))
    if num_faces <= max_faces:
        return num_faces, num_faces
    assert np.all(counts == 3), f"{mesh.GetPath()} is not a triangle mesh"
    assert mesh.GetNormalsInterpolation() == UsdGeom.Tokens.faceVarying
    points = np.asarray(mesh.GetPointsAttr().Get(), dtype=np.float64)
    faces = np.array(mesh.GetFaceVertexIndicesAttr().Get(), dtype=np.int32).reshape(-1, 3)

    subsets = UsdGeom.Subset.GetAllGeomSubsets(mesh)
    face_subset = np.full(num_faces, -1)
    for i, subset in enumerate(subsets):
        assert subset.GetElementTypeAttr().Get() == UsdGeom.Tokens.face
        face_subset[np.asarray(subset.GetIndicesAttr().Get())] = i

    o3d_mesh = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(points), o3d.utility.Vector3iVector(faces)
    )
    # Face-varying normals split vertices along creases; weld them so decimation sees a
    # connected surface instead of tearing seams open.
    o3d_mesh.remove_duplicated_vertices()
    o3d_mesh = o3d_mesh.simplify_quadric_decimation(max_faces)
    o3d_mesh.remove_degenerate_triangles()
    o3d_mesh.remove_unreferenced_vertices()
    new_points = np.asarray(o3d_mesh.vertices)
    new_faces = np.asarray(o3d_mesh.triangles)

    # Each decimated face inherits the material of the nearest original face.
    _, nearest = cKDTree(points[faces].mean(axis=1)).query(new_points[new_faces].mean(axis=1))
    new_face_subset = face_subset[nearest]

    mesh.GetPointsAttr().Set(Vt.Vec3fArray.FromNumpy(new_points.astype(np.float32)))
    mesh.GetFaceVertexCountsAttr().Set(Vt.IntArray.FromNumpy(np.full(len(new_faces), 3, np.int32)))
    mesh.GetFaceVertexIndicesAttr().Set(Vt.IntArray.FromNumpy(new_faces.ravel().astype(np.int32)))
    normals = crease_normals(new_points, new_faces, crease_deg)
    mesh.GetNormalsAttr().Set(Vt.Vec3fArray.FromNumpy(normals.astype(np.float32)))
    for i, subset in enumerate(subsets):
        indices = np.flatnonzero(new_face_subset == i).astype(np.int32)
        subset.GetIndicesAttr().Set(Vt.IntArray.FromNumpy(indices))
    extent = UsdGeom.Mesh.ComputeExtent(mesh.GetPointsAttr().Get())
    if mesh.GetExtentAttr().HasAuthoredValue():
        mesh.GetExtentAttr().Set(extent)
    return num_faces, len(new_faces)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input")
    parser.add_argument("output")
    parser.add_argument("--min-faces", type=int, default=30000)
    parser.add_argument("--ratio", type=float, default=0.1)
    parser.add_argument("--crease-deg", type=float, default=30.0)
    args = parser.parse_args()

    stage = Usd.Stage.Open(args.input)
    total_before = total_after = 0
    for prim in stage.Traverse():
        if not prim.IsA(UsdGeom.Mesh):
            continue
        before, after = decimate_mesh(UsdGeom.Mesh(prim), args.min_faces, args.ratio, args.crease_deg)
        total_before += before
        total_after += after
        if before != after:
            print(f"{prim.GetPath()}: {before} -> {after}")
    print(f"total faces: {total_before} -> {total_after}")
    stage.GetRootLayer().Export(args.output)


if __name__ == "__main__":
    main()
