"""Runnable synthetic SDK example. Never calls a simulator or a model."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PIL import Image,ImageDraw
from process_show import Recorder

def main():
    recorder=Recorder(sys.argv[1])
    recorder.start_episode(dict(run_id="example-1",attempt="1",model="synthetic-fixture",stage="example",dataset="DEMO",split="fixture",scene_id="example-room",episode_id="1",instruction="Synthetic example: move forward twice and stop. 工程示例，不是模型运行。"))
    for step in range(4):
        im=Image.new("RGB",(640,480),(20+step*40,70,100))
        ImageDraw.Draw(im).text((20,20),f"SYNTHETIC STEP {step}",fill="white")
        recorder.record_step({"step":step,"action":"RESET" if step==0 else "STOP" if step==3 else "FORWARD","pose":{"position":[0,0,-step*.25],"rotation_xyzw":[0,0,0,1]}},rgb=im)
    recorder.end_episode({"total_steps":3,"status":"completed","success":True})
    recorder.close()

if __name__=="__main__":main()
