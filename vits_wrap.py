# coding: utf-8

import os
import sys
import struct
import time
import numpy as np
import torch
from librosa import resample

from textparser import TextParser
from infer import EmoVITS
from version import __version__


def _genWavHeader(sampleNum, sampleRate=8000, bitNum=16):
    wavHeadInfo = b'\x52\x49\x46\x46'  # RIFF
    wavHeadInfo += struct.pack('i', sampleNum * 2 + 44 - 8)
    wavHeadInfo += b'\x57\x41\x56\x45\x66\x6D\x74\x20\x10\x00\x00\x00\x01\x00\x01\x00'
    wavHeadInfo += struct.pack('i', sampleRate)
    wavHeadInfo += struct.pack('i', sampleRate * bitNum // 8)
    wavHeadInfo += struct.pack('H', bitNum // 8)
    wavHeadInfo += struct.pack('H', bitNum)
    wavHeadInfo += b'\x64\x61\x74\x61'
    wavHeadInfo += struct.pack('i', sampleNum * 2)
    return wavHeadInfo


class VITSWrap(object):

    # global configuration
    default_spkid = 1
    default_volume = 1.0
    default_speed = 1.0
    default_pitch = 1.0
    default_tail_silece = 0.0 # Second

    def __init__(
        self,
        ckpt_path: str = None,
        device: torch.device = None,
        loglv: int = 0,
    ) -> None:
        self.loglv = loglv

        self.textparser = TextParser(loglv=loglv)
        self.speecher = EmoVITS(ckpt_path, device=device)
        try:
            from fbandext import NeuralFBandExt
            self.asv = NeuralFBandExt(device=device)
        except:
            self.asv = None
        
        
        self.default_sampling_rate = self.speecher.sampling_rate
        self.max_utt_length = self.textparser.max_utt_length
        print('@@@@@@@@@@@@@@@@@@@@@@@@@@')
        print(self.max_utt_length)

        if self.loglv > 0:
            func_name = f"{self.__class__.__name__}::{sys._getframe().f_code.co_name}"
            sys.stderr.write(f"{func_name}: init Successful, version={__version__} !\n")
    
    def update(self):
        self.textparser.update()
        self.speecher.update()

        # empty cuda buffer
        torch.cuda.empty_cache()
    
    def _parse_input(self, inputs):

        volume = float(inputs.get('volume', self.default_volume))
        speed = float(inputs.get('speed', self.default_speed))
        pitch = float(inputs.get('pitch', self.default_pitch))
        sampling_rate = int(inputs.get('sampling_rate', self.default_sampling_rate))
        tail_silence = float(inputs.get('tail_silence', self.default_tail_silece))

        volume = max(0., min(1., volume))
        speed = max(0.5, min(2., speed))
        pitch = max(0.5, min(2., pitch))
        sampling_rate = min(48000, max(8000, sampling_rate))

        speed /= pitch

        utt_id = inputs.get('id', str(time.time()).replace('.', '_'))
        utt_text = inputs.get('text', '。')
        spkid = int(inputs.get('spkid', self.default_spkid))
        emotion = inputs.get('emotion')

        return inputs, utt_id, utt_text, spkid, volume, speed, pitch, sampling_rate, tail_silence, emotion

    def _handle_outputs(self, inputs, wav_bytes, sampling_rate, segment_info, time_used_frontend, time_used_backend, rtf):
        outputs = inputs
        outputs['wav'] = _genWavHeader(len(wav_bytes)//2, sampling_rate, 16) + wav_bytes
        outputs['sr'] = sampling_rate
        outputs['segment_info'] = segment_info
        outputs['time_used_frontend'] = time_used_frontend * 1000  # ms
        outputs['time_used_backend'] = time_used_backend * 1000  # ms
        outputs['rtf'] = rtf
        return outputs
    
    def _split_utt_text(self, utt_id, utt_text):
        if utt_text is None or utt_text == '':
            utt_text = '。'
        utt_text = utt_text.strip()
        print('@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@')
        print(utt_text)
        print(len(utt_text))
        if len(utt_text) <= self.max_utt_length:
            return [utt_id], [utt_text]
        
        center_pos = int(self.max_utt_length * 0.618)
        max_length = self.max_utt_length
        def find_nearest_center(texts):
            if len(texts) < center_pos + 2: return len(texts)
            if len(texts) >= center_pos + 2 and texts[center_pos:center_pos+2] in ['——', '……']:
                return center_pos + 2
            for _chr in ['。', '！', '!', '？', '?', '；', ';', '，']:
                find_pos = texts[:center_pos][::-1].find(_chr)
                if 0 <= find_pos < center_pos:
                    return center_pos - find_pos
                find_pos = texts.find(_chr, center_pos)
                if 0 <= find_pos < max_length:
                    return find_pos + len(_chr)
            for _chr in ['.', ',', ':', '：']:
                texts_reverse = texts[:center_pos][::-1]
                cl = len(_chr)
                find_pos = texts_reverse.find(_chr)
                if (0 <= find_pos < center_pos
                    and (find_pos - cl >= 0 and not (texts_reverse[find_pos-cl].isdigit()))
                    and (find_pos + cl < center_pos and not (texts_reverse[find_pos+cl].isdigit()))
                ):
                    return center_pos - find_pos
                find_pos = texts.find(_chr, center_pos)
                if (0 <= find_pos < max_length
                    and (find_pos - cl >= 0 and not (texts[find_pos-cl].isdigit()))
                    and (find_pos + cl < len(texts) and not (texts[find_pos+cl].isdigit()))
                ):
                    return find_pos + cl
            for _chr in ['——', '……', '、', '（', '）', '(', ')', '[', ']', '【', '】']:
                find_pos = texts[:center_pos][::-1].find(_chr)
                if 0 <= find_pos < center_pos:
                    return center_pos - find_pos
                find_pos = texts.find(_chr, center_pos)
                if 0 <= find_pos < max_length:
                    return find_pos + len(_chr)
            for _chr in ['~', ' ', '\t']:
                find_pos = texts[:center_pos][::-1].find(_chr)
                if 0 <= find_pos < center_pos:
                    return center_pos - find_pos
                find_pos = texts.find(_chr, center_pos)
                if 0 <= find_pos < max_length:
                    return find_pos + len(_chr)
            return min(len(texts), max_length)
        
        batch_utt_id, batch_utt_text = [], []
        i = 0
        while len(utt_text) > 0:
            pos = find_nearest_center(utt_text)
            if pos > self.max_utt_length:
                pos = self.max_utt_length - 1
                batch_utt_text.append(utt_text[:pos] + "，")
            else:
                batch_utt_text.append(utt_text[:pos])
            batch_utt_id.append(f"{utt_id}-{i}")
            utt_text = utt_text[pos:]
            i += 1
        
        return batch_utt_id, batch_utt_text

    @torch.no_grad()
    def speaking(self, inputs : dict) -> dict:
        inputs, utt_id, utt_text, spkid, volume, speed, pitch, sampling_rate, tail_silence, emotion = \
            self._parse_input(inputs)
        
        batch_utt_id, batch_utt_text = self._split_utt_text(utt_id, utt_text)
        batch_wav, batch_wavlen = [], 0
        segment_info, start_ms, end_ms = [], 0, 0
        time_used_frontend, time_used_backend = 0, 0
        for idx, (utt_id, utt_text) in enumerate(zip(batch_utt_id, batch_utt_text), 1):
            start = time.time()
            utt_id, utt_segtext, utt_vector = self.textparser(utt_id, utt_text)
            print('@@@@@@@@@@@@@@@@@@@@@@@@@')
            print('utt_id,', utt_id)
            utt_segtext = utt_segtext.printer()
            print('utt_segtext1, ', utt_segtext)
            #if 'AA/(ae)1-(aa)1' in utt_segtext:
            #    print('warning!!!')
            #    utt_segtext = utt_segtext.replace('AA/(ae)1-(aa)1', 'AA/(ey)1-(ey)1') # AA/(ae)1-(aa)1
            #print('utt_segtext, ', utt_segtext)
            print('utt_vector', utt_vector)
            end = time.time()
            time_used_frontend += end - start

            start = end
            wav, emotion = self.speecher.infer(spkid, utt_vector, emotion, duration_rate=speed)
            batch_wavlen += len(wav)
            if pitch != 1.0:
                wav = resample(wav, orig_sr=int(self.default_sampling_rate/pitch), target_sr=self.default_sampling_rate)
            sr = self.default_sampling_rate
            if sampling_rate > sr and self.asv is not None:
                wav = np.expand_dims(wav, axis=0)
                wav, sr = self.asv.infer(wav, sr)
                wav = np.squeeze(wav, axis=0)
            if sampling_rate != sr:
                wav = resample(wav, orig_sr=sr, target_sr=sampling_rate)
            wav = np.clip(wav * volume * 32767, -32768, 32767).astype(np.int16)
            if tail_silence > 0:
                wav = np.pad(wav, [0, int(tail_silence*sampling_rate)])
            batch_wav.append(wav)
            end = time.time()
            time_used_backend += end - start
            
            end_ms += len(wav) / sampling_rate * 1000
            segment_info.append({
                "start_ms": start_ms,
                "end_ms": end_ms,
                "input_text": utt_text,
                "segtext": utt_segtext,
            })
            start_ms = end_ms
        
        rtf = (time_used_frontend + time_used_backend) / (batch_wavlen / self.default_sampling_rate)
        batch_wav_bytes = bytes()
        for idx, wav in enumerate(batch_wav, 1):
            batch_wav_bytes += wav.tobytes()

        outputs = self._handle_outputs(
            inputs, batch_wav_bytes, sampling_rate, segment_info, time_used_frontend, time_used_backend, rtf)
        return outputs



if __name__ == "__main__":
    
    import argparse
    
    loglv = 0
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--device', "-d", type=str, required=False, default=None,
                        help='Use cuda or cpu.')
    parser.add_argument("--checkpoint", "-c", default=None, type=str,
                        help="checkpoint file to be loaded.")
    parser.add_argument("--emotion", "-e", default=None, type=str,
                        help="speaker Id or emotion file path. format: [spkid|path]:eid, "
                             "which `path` is emotion embedding file, `eid` is index.")
    parser.add_argument('--utterance', '-u', type=str, required=False,
                        help='Input utterance with UTF-8 encoding to synthesize.')
    parser.add_argument('--textfile', '-t', type=str, required=False,
                        help='Input text file with UTF-8 encoding to synthesize.')
    parser.add_argument('--spkid', '--sid', '-i', type=int, required=False, default=1,
                        help='Set speaker ID. (default=1)')
    parser.add_argument('--volume', '-v', type=float, required=False, default=1.0,
                        help='Set volume, its range is (0.0, 1.0]. (default=1.0)')
    parser.add_argument('--speed', '-s', type=float, required=False, default=1.0,
                        help='Set speed, its range is (0.5, 1.0]. (default=1.0)')
    parser.add_argument('--pitch', '-p', type=float, required=False, default=1.0,
                        help='Set pitch, its range is (0.0, 1.0]. (default=1.0)')
    parser.add_argument('--tail-silence', '-a', type=float, required=False, default=0.0,
                        help='Set tail silence in second. (default=0.0)')
    parser.add_argument('--sampling-rate', '-r', type=int, required=False,
                        help='Set sampling rate.')
    parser.add_argument('--outdir', '-o', type=str, required=True,
                        help='Directory for saving synthetic wav.')
    parser.add_argument('--outfn', '-n', type=str, required=True,
                        help='Filename for saving synthetic wav.')
    parser.add_argument('--loglv', '-l', type=int, required=False, default=loglv,
                        help='Log level. (default={})'.format(loglv))
    args = parser.parse_args()
    
    # check args
    if args.utterance is None and args.textfile is None:
        raise ValueError("Please specify either --utterance or --textfile")
    
    if not os.path.exists(args.outdir):
        os.makedirs(args.outdir)
        
    # construct tts instance
    mytts = VITSWrap(args.checkpoint, device=args.device, loglv=args.loglv)
    
    # set emotion
    emotion = args.emotion.split(':') if args.emotion is not None else None
    if emotion is not None:
        if isinstance(emotion[0], str) and os.path.exists(emotion[0]):
            emotion[0] = np.fromfile(emotion[0], dtype=np.float32).reshape(-1, 1024)
        else:
            emotion[0] = int(emotion[0])
        if len(emotion) == 1:
            emotion.append(-1)
        else:
            emotion[1] = int(emotion[1])
        emotion = tuple(emotion)
    
    # pack inputs
    inputs = {
        "spkid": args.spkid,
        "volume": args.volume,
        "speed": args.speed,
        "pitch": args.pitch,
        "emotion": emotion,
    }
    if args.sampling_rate is not None:
        inputs["sampling_rate"] = args.sampling_rate
    if args.tail_silence is not None:
        inputs["tail_silence"] = max(0, args.tail_silence)
    
    utt_text = []
    if args.utterance is not None:
        utt_text.append(args.utterance)
    if args.textfile is not None:
        with open(args.textfile, 'rt') as f:
            for line in f:
                line = line.strip()
                if len(line) == 0: continue
                utt_text.append(line)
    
    # syntheize
    outfn = "" if args.outfn is None else args.outfn
    if len(outfn) > 0 and outfn[-4:].lower() != ".wav":
        for idx, text in enumerate(utt_text, 1):
            inputs["text"] = text
            print("To synthesize:\n", inputs)
            outputs = mytts.speaking(inputs)    
            wav = outputs.pop('wav')
            print(outputs)
            with open(os.path.join(args.outdir, f"{outfn}{idx:06d}.wav"), 'wb') as f:
                f.write(wav)
    else:
        inputs["text"] = " ".join(utt_text)
        print("To synthesize:\n", inputs)
        outputs = mytts.speaking(inputs)    
        wav = outputs.pop('wav')
        print(outputs)
        with open(os.path.join(args.outdir, outfn), 'wb') as f:
            f.write(wav)
    
    print("Done!")
        
