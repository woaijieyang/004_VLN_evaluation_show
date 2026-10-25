"""CPU z-buffer rendering of actual GLB geometry and supported textures.

The roof is cut at a fixed height above the episode floor. No inferred objects,
no new observations, no model inference, no OpenGL/renderer/GPU context.
"""
import io,json,struct
import numpy as np
from PIL import Image,ImageDraw


def read_glb(path):
    raw=path.read_bytes()
    magic,version,length=struct.unpack_from('<4sII',raw)
    if magic!=b'glTF' or version!=2 or length!=len(raw):raise ValueError('Expected binary glTF 2')
    offset=12;doc=None;binary=None
    while offset<len(raw):
        size,kind=struct.unpack_from('<II',raw,offset);offset+=8
        if kind==0x4E4F534A:doc=json.loads(raw[offset:offset+size])
        elif kind==0x004E4942:binary=memoryview(raw)[offset:offset+size]
        offset+=size
    if doc is None or binary is None:raise ValueError('GLB chunks incomplete')
    return doc,binary


def accessor(doc,binary,index):
    item=doc['accessors'][index]
    if 'sparse' in item:raise ValueError('Sparse GLB accessors unsupported')
    view=doc['bufferViews'][item['bufferView']]
    dtype=np.dtype({5120:'i1',5121:'u1',5122:'<i2',5123:'<u2',5125:'<u4',5126:'<f4'}[item['componentType']])
    width={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}[item['type']]
    array=np.ndarray((item['count'],width),dtype=dtype,buffer=binary,offset=view.get('byteOffset',0)+item.get('byteOffset',0),strides=(view.get('byteStride',width*dtype.itemsize),dtype.itemsize))
    if item.get('normalized') and dtype.kind in 'iu':array=array.astype('f4')/np.iinfo(dtype).max
    return array


def node_matrix(node):
    if 'matrix' in node:return np.array(node['matrix'],dtype='f4').reshape(4,4,order='F')
    x,y,z,w=node.get('rotation',[0,0,0,1])
    rotation=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                       [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                       [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]],dtype='f4')
    matrix=np.eye(4,dtype='f4');matrix[:3,:3]=rotation@np.diag(node.get('scale',[1,1,1]));matrix[:3,3]=node.get('translation',[0,0,0])
    return matrix


def clip_y(vertices,limit,keep_above):
    result=[]
    for a,b in zip(vertices,vertices[1:]+vertices[:1]):
        ia=a[1]>=limit if keep_above else a[1]<=limit
        ib=b[1]>=limit if keep_above else b[1]<=limit
        if ia:result.append(a)
        if ia!=ib:
            t=(limit-a[1])/(b[1]-a[1]);result.append(a+t*(b-a))
    return result


def textured_topdown(path,bounds,floor_y,max_side=1400,cut_height=1.7,world_transform=None):
    doc,binary=read_glb(path)
    minx,minz,maxx,maxz=bounds
    mpp=max(maxx-minx,maxz-minz)/max_side
    width=max(1,int(np.ceil((maxx-minx)/mpp)));height=max(1,int(np.ceil((maxz-minz)/mpp)))
    pixels=np.full((height,width,3),244,dtype='u1')
    depth=np.full((height,width),-np.inf,dtype='f4')
    bottom,top=floor_y-.35,floor_y+cut_height
    caps=[];rendered=0;basis_textures=0;geometry_bounds=[]

    def raster(tri,texture,factor):
        nonlocal rendered
        p=np.array(tri,dtype='f4')
        sx=(p[:,0]-minx)/mpp;sy=(p[:,2]-minz)/mpp
        x0=max(0,int(np.floor(sx.min())));x1=min(width-1,int(np.ceil(sx.max())))
        y0=max(0,int(np.floor(sy.min())));y1=min(height-1,int(np.ceil(sy.max())))
        if x1<x0 or y1<y0:return
        denom=(sy[1]-sy[2])*(sx[0]-sx[2])+(sx[2]-sx[1])*(sy[0]-sy[2])
        if abs(denom)<1e-5:return
        xx=np.arange(x0,x1+1,dtype='f4')[None,:]+.5
        yy=np.arange(y0,y1+1,dtype='f4')[:,None]+.5
        a=((sy[1]-sy[2])*(xx-sx[2])+(sx[2]-sx[1])*(yy-sy[2]))/denom
        b=((sy[2]-sy[0])*(xx-sx[2])+(sx[0]-sx[2])*(yy-sy[2]))/denom
        c=1-a-b
        yy3=a*p[0,1]+b*p[1,1]+c*p[2,1]
        target=depth[y0:y1+1,x0:x1+1]
        mask=(a>=-1e-5)&(b>=-1e-5)&(c>=-1e-5)&(yy3>target)
        if not np.any(mask):return
        target[mask]=yy3[mask]
        if texture is not None:
            u=(a*p[0,3]+b*p[1,3]+c*p[2,3])[mask]
            v=(a*p[0,4]+b*p[1,4]+c*p[2,4])[mask]
            tx=np.minimum((np.mod(u,1)*texture.shape[1]).astype(int),texture.shape[1]-1)
            ty=np.minimum((np.mod(v,1)*texture.shape[0]).astype(int),texture.shape[0]-1)
            color=texture[ty,tx,:3].astype('f4')*factor
        else:
            elevation=np.clip((yy3[mask]-floor_y)/cut_height,0,1)[:,None]
            color=np.array([238,237,229])*(1-elevation)+np.array([126,139,153])*elevation
            color=color*factor
        # Mild neutral lift preserves actual texture while keeping paths legible.
        color=np.clip(color*.92+12,0,255).astype('u1')
        pixels[y0:y1+1,x0:x1+1][mask]=color
        rendered+=1

    def draw_mesh(index,matrix):
        nonlocal basis_textures
        for primitive in doc['meshes'][index]['primitives']:
            if primitive.get('mode',4)!=4:continue
            pos=accessor(doc,binary,primitive['attributes']['POSITION'])
            pos=pos@matrix[:3,:3].T+matrix[:3,3]
            geometry_bounds.append((pos.min(axis=0),pos.max(axis=0)))
            uv=accessor(doc,binary,primitive['attributes']['TEXCOORD_0']) if 'TEXCOORD_0' in primitive['attributes'] else np.zeros((len(pos),2),dtype='f4')
            indices=accessor(doc,binary,primitive['indices']).reshape(-1,3) if 'indices' in primitive else np.arange(len(pos)).reshape(-1,3)
            triangles=pos[indices]
            valid=(triangles[:,:,1].max(axis=1)>=bottom)&(triangles[:,:,1].min(axis=1)<=top)
            indices=indices[valid]
            material=doc.get('materials',[{}])[primitive.get('material',0)].get('pbrMetallicRoughness',{})
            factor=np.array(material.get('baseColorFactor',[1,1,1,1])[:3],dtype='f4')
            texture=None
            if 'baseColorTexture' in material:
                texture_info=doc['textures'][material['baseColorTexture']['index']]
                if 'source' not in texture_info:
                    basis_textures+=1  # Geometry is still exact; report height shading honestly.
                else:
                    source=doc['images'][texture_info['source']]
                    if 'bufferView' not in source:raise ValueError('External texture URI not permitted')
                    view=doc['bufferViews'][source['bufferView']];offset=view.get('byteOffset',0)
                    with Image.open(io.BytesIO(binary[offset:offset+view['byteLength']])) as im:
                        im=im.convert('RGB');im.thumbnail((2048,2048));texture=np.asarray(im)
            for ids in indices:
                points=[np.concatenate((pos[i],uv[i])) for i in ids]
                # Explicit roof-cut intersection shows wall cross-sections as real geometry.
                crossing=[]
                for a,b in zip(points,points[1:]+points[:1]):
                    if (a[1]<top)!=(b[1]<top):
                        t=(top-a[1])/(b[1]-a[1]);p=a+t*(b-a)
                        crossing.append(((p[0]-minx)/mpp,(p[2]-minz)/mpp))
                if len(crossing)==2:caps.append(crossing)
                clipped=clip_y(clip_y(points,bottom,True),top,False)
                for i in range(1,len(clipped)-1):raster([clipped[0],clipped[i],clipped[i+1]],texture,factor)

    def visit(index,parent):
        node=doc['nodes'][index];matrix=parent@node_matrix(node)
        if 'mesh' in node:draw_mesh(node['mesh'],matrix)
        for child in node.get('children',[]):visit(child,matrix)
    root_matrix=np.eye(4,dtype='f4') if world_transform is None else np.array(world_transform,dtype='f4')
    for index in doc['scenes'][doc.get('scene',0)]['nodes']:visit(index,root_matrix)
    finite=np.isfinite(depth)
    edges=np.zeros_like(finite)
    for shift in ((1,0),(-1,0),(0,1),(0,-1)):
        neighbor=np.roll(depth,shift,(0,1));other=np.isfinite(neighbor)
        edges |= finite & (~other | (np.abs(np.where(finite,depth,0)-np.where(other,neighbor,0))>.10))
    pixels[edges]=(pixels[edges].astype('f4')*.68).astype('u1')
    image=Image.fromarray(pixels);draw=ImageDraw.Draw(image)
    for segment in caps:draw.line(segment,fill=(85,99,110),width=2)
    return image,{'bounds':[minx,minz,minx+width*mpp,minz+height*mpp],
                  'meters_per_pixel':mpp,'width':width,'height':height,
                  'floor_y':floor_y,'cut_height':cut_height,'rendered_triangles':rendered,
                  'color_mode':'geometry_height_shading' if basis_textures else 'texture',
                  'basis_textures_unavailable':basis_textures,
                  'geometry_bounds':[np.min([p[0] for p in geometry_bounds],axis=0).tolist(),np.max([p[1] for p in geometry_bounds],axis=0).tolist()],
                  'world_transform':root_matrix.tolist(),
                  'map_transform':[[1/mpp,0,-minx/mpp],[0,1/mpp,-minz/mpp]]}
