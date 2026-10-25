"""The same composition is used for live previews and every exported frame."""
import math
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont, ImageOps

WIDTH, HEIGHT = 1920, 1080
BG = "#ffffff"
FG = "#111111"
MUTED = "#111111"
CYAN = "#111111"


@lru_cache(maxsize=32)
def font(size, bold=False):
    configured = os.environ.get("PROCESS_SHOW_FONT")
    candidates = [configured] if configured else []
    candidates += [str(Path(__file__).resolve().parents[2]/".assets"/"NotoSansCJKsc-Regular.otf"), "C:/Windows/Fonts/msyh.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
    for p in candidates:
        if p and Path(p).is_file():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def wrap(text, f, width):
    # Preserve explicit newlines and wrap all scripts without dropping characters.
    out = []
    for paragraph in str(text).split("\n"):
        line = ""
        for ch in paragraph:
            if f.getlength(line+ch) > width and line:
                out.append(line)
                line = ch
            else:
                line += ch
        out.append(line)
    return out


@lru_cache(maxsize=128)
def instruction_pages(instruction):
    lines = wrap(instruction or "Instruction unavailable / 未记录指令", font(32), 1790)
    return [lines[i:i+4] for i in range(0, len(lines), 4)] or [[""]]


def fit(canvas, image, box):
    image = image.convert("RGB")
    thumb = ImageOps.contain(image, (box[2]-box[0], box[3]-box[1]), Image.Resampling.LANCZOS)
    canvas.paste(thumb, (box[0]+(box[2]-box[0]-thumb.width)//2, box[1]+(box[3]-box[1]-thumb.height)//2))


def heading(q):
    x, y, z, w = q
    norm = math.sqrt(x*x+y*y+z*z+w*w)
    x,y,z,w = [v/norm for v in (x,y,z,w)]
    return (-2*(x*z+w*y), -(1-2*(x*x+y*y)))  # world x,z of camera forward -z


def compose(meta, step, history, rgb=None, map_image=None, page=0):
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    d = ImageDraw.Draw(image)
    small, body, title = font(23), font(27), font(34)
    d.text((40, 23), "process-show", font=title, fill=FG)
    identity = f"{meta['dataset']}  /  {meta['scene_id']}  /  EP {meta['episode_id']}  /  {meta['stage']}"
    d.text((380, 30), identity[:105], font=body, fill=MUTED)
    d.rounded_rectangle((40, 96, 1170, 725), radius=18, fill="#080e17")
    d.rounded_rectangle((1190, 96, 1880, 725), radius=18, fill="#edf3f5")
    d.text((63, 115), "FIRST-PERSON OBSERVATION / 真实视角", font=small, fill=MUTED)
    if rgb is not None:
        fit(image, rgb, (58, 155, 1152, 710))
        if step.get('display_rgb_step') is not None and step['display_rgb_step']!=step['step']:
            d.rectangle((65,655,1145,705),fill='#111a25')
            d.text((82,665),f"当前 STEP {step['step']} · 已记录观测 #{step['display_rgb_step']}（非当前步原始帧）",font=font(25),fill='#eef3fa')
    else:
        d.text((280, 380), "该步未采集图像", font=font(40), fill=MUTED)
        d.text((282, 445), "No recorded RGB for this step", font=body, fill=MUTED)
    d.text((1210, 115), "BEV / 俯视轨迹", font=body, fill="#253c4d")
    pose = step.get("pose") or {}
    floor = step.get("floor_id")
    candidates = [s for s in history if s["step"] <= step["step"] and s.get("pose")]
    candidates = [s for s in candidates if s.get("floor_id") == floor] if floor is not None else candidates
    bbox = (1220, 175, 1850, 650)
    transform = step.get("map_transform") or meta.get("map_transform")
    if map_image is not None and transform:
        fit(image, map_image, bbox)
        scale = min((bbox[2]-bbox[0])/map_image.width, (bbox[3]-bbox[1])/map_image.height)
        offset = (bbox[0]+((bbox[2]-bbox[0])-map_image.width*scale)/2, bbox[1]+((bbox[3]-bbox[1])-map_image.height*scale)/2)
        def project(p):
            u = transform[0][0]*p[0]+transform[0][1]*p[2]+transform[0][2]
            v = transform[1][0]*p[0]+transform[1][1]*p[2]+transform[1][2]
            return offset[0]+u*scale, offset[1]+v*scale
        pixels_per_meter = math.hypot(transform[0][0], transform[1][0])*scale
        label = "真实场景几何 · 墙体 / 家具 / 障碍物" if step.get('scene_geometry') else "Recorded map / 已记录底图"
    else:
        points = [pose["position"]] if pose else []
        if points:
            minx,maxx = min(p[0] for p in points),max(p[0] for p in points)
            minz,maxz = min(p[2] for p in points),max(p[2] for p in points)
        else:
            minx,maxx,minz,maxz = -2,2,-2,2
        cx,cz = (minx+maxx)/2,(minz+maxz)/2
        pixels_per_meter = min(530/8,375/8)
        def project(p):
            return 1535+(p[0]-cx)*pixels_per_meter,412+(p[2]-cz)*pixels_per_meter
        for gx in range(1220, 1851, 50):
            d.line((gx,175,gx,650), fill="#dae5e9")
        for gy in range(175, 651, 50):
            d.line((1220,gy,1850,gy), fill="#dae5e9")
        label = "无场景底图 / trajectory only"
    from process_show.media.export_overlay import annotate
    anchors=meta.get('_anchors',{'start':history[0].get('pose',{}).get('position') if history else None,'goals':meta.get('goals',[])})
    annotate(image,bbox,project,candidates,step,anchors,font)
    if pose:
        p=pose['position']
        d.text((1210,660),f"x {p[0]:.2f}   y {p[1]:.2f}   z {p[2]:.2f} m",font=small,fill='#000')
    d.text((1210, 691), label + (f" | floor {floor}" if floor is not None else " | 未分层"), font=font(20), fill="#536b7b")
    bar = min(100,pixels_per_meter)
    d.line((1235,620,1235+bar,620), fill="#253c4d", width=4)
    d.text((1235,584), f"{bar/pixels_per_meter:.2g} m", font=font(18), fill="#253c4d")
    d.text((48, 749), f"STEP {step['step']:04d}", font=font(38), fill=CYAN)
    action = str(step.get("action", "RESET"))
    details = f"ACTION  {action}     MODEL CALL  {step.get('call_id', '—')}     COLLISION  {'YES' if step.get('collided') else 'NO' if step.get('collided') is False else '—'}"
    d.text((340, 758), details[:105], font=body, fill=FG)
    pages = instruction_pages(meta.get("instruction", ""))
    page %= len(pages)
    d.rounded_rectangle((40, 823, 1880, 1055), radius=18, fill="#f4f7ff")
    d.text((64, 838), f"INSTRUCTION / 导航指令    {page+1}/{len(pages)}", font=small, fill=CYAN)
    for i,line in enumerate(pages[page]):
        d.text((64, 879+i*39), line, font=font(32), fill=FG)
    return image


def ffmpeg_exe():
    p = os.environ.get("PROCESS_SHOW_FFMPEG") or shutil.which("ffmpeg")
    if p:
        return p
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError("FFmpeg unavailable. Set PROCESS_SHOW_FFMPEG or install plugin dependencies in its own environment")


_ENCODER = threading.Lock()
_PREVIEW = threading.Lock()


class RenderDeferred(Exception):
    """A user-requested export takes priority at a safe preview boundary."""


def ensure_preview(store,eid,number):
    """On-demand rendering for read-only traces; no simulator or model needed."""
    from process_show.storage.core import dumps
    from process_show.capture.engine import open_asset
    with _PREVIEW:
        row=store.get(eid)
        history=store.steps(eid)
        step=next((s for s in history if s["step"]==number),None)
        if step is None:raise KeyError(number)
        if step.get('presentation_version')==4 and step.get("previews") and all(store.path(eid,p).is_file() for p in step["previews"]):
            return step["previews"]
        from process_show.playback.presentation import observation_image
        rgb,rgb_step=observation_image(store,eid,number)
        if step.get("rgb_available") and rgb is None:
            raise ValueError("Recorded source image unavailable; refusing silent substitution")
        from process_show.playback.anchors import resolve_anchors
        visual_meta=dict(row['meta'],_anchors=resolve_anchors(row['meta'],history))
        visual=dict(step,display_rgb_step=rgb_step)
        map_image=None
        if step.get('map_asset'):
            with Image.open(store.path(eid,step['map_asset'])) as im:map_image=im.convert('RGB').copy()
        elif row['meta'].get('source_kind') in ('case1_trace','live_sensor') and row['meta'].get('source_run') and step.get('pose'):
            from process_show.maps.scene_map import maps_for
            info=maps_for(store).scene(row['meta'],step['pose']['position'][1])
            if info.get('available'):
                with Image.open(store.root/'_maps'/(info['key']+'.webp')) as im:map_image=im.convert('RGB').copy()
                visual['map_transform']=info['map_transform'];visual['scene_geometry']=True
                # Keep the scene extent; browser zoom is controlled by the user.

        store.capacity()
        store.path(eid,"previews").mkdir(exist_ok=True)
        names=[]
        for page in range(len(instruction_pages(row["meta"]["instruction"]))):
            name=f"previews/{number:07d}-{page:03d}.webp"
            path=store.path(eid,name);temp=path.with_suffix(".tmp")
            store.own(eid,path);store.own(eid,temp)
            compose(visual_meta,visual,history,rgb,map_image,page=page).save(temp,"WEBP",quality=85,method=2)
            temp.replace(path);names.append(name)
        with store.db() as db:
            current=json.loads(db.execute("SELECT data FROM steps WHERE episode=? AND step=?",(eid,number)).fetchone()[0])
            if current != step:
                raise ValueError("Step updated during preview rendering; retry")
            current["previews"]=names
            current['presentation_version']=4
            db.execute("UPDATE steps SET data=? WHERE episode=? AND step=?",(dumps(current),eid,number))
        return names


def encode(store, eid):
    """Global process lock plus filesystem lock: one CPU encoder per output root."""
    import contextlib
    import json
    import time
    row = store.get(eid)
    if row["status"] == "ready":
        return
    with _ENCODER:
        lockpath = store.root / "encoder.lock"
        with lockpath.open("a+b") as lock:
            from process_show.service.filelock import lock as lock_file
            lock_file(lock)
            row = store.get(eid)
            if row["status"] == "ready" or row["deleted"] is not None:
                return
            with store.db() as db:
                db.execute("UPDATE episodes SET status='encoding',updated=? WHERE id=?",(store.clock(),eid))
            steps = store.steps(eid)
            if not steps:
                raise ValueError("No steps to encode")
            for index,step in enumerate(steps):
                if index%16==0:
                    with store.db() as db:
                        own=db.execute('SELECT 1 FROM render_requests WHERE episode=?',(eid,)).fetchone()
                        priority=db.execute("SELECT 1 FROM render_requests r JOIN episodes e ON e.id=r.episode WHERE r.episode!=? AND e.status IN ('pending_encode','encoding') AND e.deleted IS NULL",(eid,)).fetchone()
                    if not own and priority:
                        with store.db() as db:
                            db.execute("UPDATE episodes SET status='pending_encode' WHERE id=?",(eid,))
                        raise RenderDeferred('User-requested export queued')
                if step.get('presentation_version')!=4 or not step.get("previews"):
                    ensure_preview(store,eid,step["step"])
            steps=store.steps(eid)
            preview_paths = []
            index = []
            frame_no = 0
            for step in steps:
                files = step.get("previews", [])
                if not files:
                    raise ValueError(f"Missing preview at step {step['step']}")
                start = frame_no / 2
                for file in files:
                    repeats=4 if len(files)>1 else 1
                    preview_paths.extend([store.path(eid, file)]*repeats)
                    frame_no += repeats
                index.append({"step": step["step"], "start": start, "end": frame_no/2, "frame": int(start*2), "pages": len(files)})
            final = store.path(eid, Path(row["directory"]).name+".mp4")
            tmp = final.with_suffix(".partial.mp4")
            log = store.path(eid, "encoder.log")
            for path in (final,tmp,log):
                store.own(eid,path)
            with store.db() as db:
                db.execute("UPDATE episodes SET status='encoding' WHERE id=?", (eid,))
            started = time.monotonic()
            try:
                command = [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", "-threads", "2", "-filter_threads", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", "2", "-i", "pipe:0", "-an", "-c:v", "libx264", "-threads", "2", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-g", "8", "-movflags", "+faststart", str(tmp)]
                with log.open("wb") as err:
                    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
                    try:
                        for path in preview_paths:
                            store.capacity()
                            with Image.open(path) as im:
                                process.stdin.write(im.convert("RGB").tobytes())
                        process.stdin.close()
                        if process.wait(timeout=300):
                            raise RuntimeError("FFmpeg encode failed; see encoder.log")
                    finally:
                        if process.poll() is None:
                            process.kill()
                            process.wait()
                # Decode the entire stream and compare decoded frame count.
                verify = subprocess.run([ffmpeg_exe(), "-v", "error", "-threads", "2", "-i", str(tmp), "-map", "0:v:0", "-f", "framemd5", "-"], capture_output=True, timeout=300, check=True)
                decoded = sum(1 for line in verify.stdout.splitlines() if line and not line.startswith(b"#"))
                if decoded != frame_no or tmp.stat().st_size == 0:
                    raise RuntimeError("Encoded frame count mismatch")
                os.replace(tmp, final)
                store.save_json(eid, "steps.json", {"fps": 2, "duration": frame_no/2, "index": index, "steps": steps})
                with Image.open(preview_paths[0]) as first:
                    first.thumbnail((640,360))
                    thumb = store.path(eid, "thumbnail.webp")
                    store.own(eid,thumb)
                    first.save(thumb, "WEBP", quality=80)
                store.save_json(eid, "encoding.json", {"frames": frame_no, "seconds": time.monotonic()-started, "bytes": final.stat().st_size, "decoded_frames": decoded, "encoder": "libx264", "crf": 23, "threads": 2,'presentation_version':4})
                with store.db() as db:
                    meta=json.loads(db.execute('SELECT meta FROM episodes WHERE id=?',(eid,)).fetchone()[0]);meta['video_version']=4
                    db.execute("UPDATE episodes SET status='ready',ready=?,updated=?,error='',meta=? WHERE id=?", (store.clock(),store.clock(),json.dumps(meta,ensure_ascii=False),eid))
                    db.execute('DELETE FROM render_requests WHERE episode=?',(eid,))
                store.save_json(eid,'episode.json',meta)
                # Only owned preview files are removed, after validated atomic commit.
                for path in preview_paths:
                    path.unlink(missing_ok=True)
                store.cleanup(dry_run=True)
            except Exception as exc:
                with store.db() as db:
                    db.execute("UPDATE episodes SET status='encode_error',error=?,updated=? WHERE id=?", (str(exc),store.clock(),eid))
                raise


def extract_frame(store, eid, step):
    import json
    row = store.get(eid)
    with store.path(eid, "steps.json").open(encoding="utf-8") as f:
        index = json.load(f)["index"]
    match = next((v for v in index if v["step"] == step), None)
    if match is None:
        raise KeyError(step)
    video = store.path(eid, Path(row["directory"]).name+".mp4")
    # Decode from the preceding keyframe, discard until target presentation timestamp.
    command = [ffmpeg_exe(), "-v", "error", "-threads", "2", "-ss", str(match["start"]), "-i", str(video), "-frames:v", "1", "-f", "image2pipe", "-c:v", "mjpeg", "-threads", "1", "-"]
    return subprocess.run(command, capture_output=True, timeout=30, check=True).stdout
