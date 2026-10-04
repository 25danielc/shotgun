#!/bin/sh
# QA on the rendered video: streams, duration, contact sheet, 640x360 legibility frames, loudness.
#   sh video/tools/qa.sh
set -e
cd "$(dirname "$0")/.."
OUT=out/shotgun_demo_1080p.mp4
Q=work/qa; rm -rf "$Q"; mkdir -p "$Q/small"
ffprobe -v error -show_entries stream=codec_name,profile,width,height,pix_fmt,r_frame_rate,sample_rate,bit_rate:format=duration -of compact "$OUT"
ffmpeg -v error -y -i "$OUT" -vf "fps=2,scale=320:-1,tile=10x20" -frames:v 1 "$Q/contact_sheet.png"
# every headline settle, callout and caption, downscaled to 640x360
for t in $(python3 -c "
import json;T=json.loads(open('comp/timeline.js').read().split('window.TL = ',1)[1].split(';\nwindow.CAPS',1)[0])
ts=[]
def walk(n):
    if isinstance(n,dict):
        if 'text' in n and 't' in n and isinstance(n['t'],(int,float)): ts.append(n['t']+0.9)
        for v in n.values(): walk(v)
    elif isinstance(n,list):
        for v in n: walk(v)
walk({k:v for k,v in T.items() if k!='intro'})
print(' '.join(f'{t:.2f}' for t in sorted(set(round(x,1) for x in ts)) if t<99.7))"); do
  ffmpeg -v error -y -ss "$t" -i "$OUT" -frames:v 1 -vf scale=640:360 "$Q/small/t$t.png"
done
ffmpeg -v error -y -pattern_type glob -i "$Q/small/*.png" -vf "tile=4x6" "$Q/small_%02d.png"
ffmpeg -hide_banner -nostats -i "$OUT" -af ebur128=peak=true -f null - 2>&1 | sed -n '/Summary/,$p'
ffmpeg -v error -y -i "$OUT" -filter_complex "showwavespic=s=1920x240:split_channels=0:colors=#39ff9c" -frames:v 1 "$Q/waveform.png"
ls "$Q"
