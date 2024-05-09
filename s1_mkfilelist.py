import os

with open('files.scp', 'r') as fr:
    lines = fr.readlines()
with open('train.scp', 'w') as fw:
    for line in lines:
        line = line.strip('\n')
        vec = line.replace('.wav', '.vec256')
        emo = line.replace('.wav', '.emo')
        #emo = '/data/jiangda/CODE/yuanwuwen/VITS/emotional-vits3/checkpoint/yuanzhongwen/916.emo'
        speaker_id = '2047' #'916'
        fw.write(vec + '|' + line + '|' +emo + '|' + speaker_id + '\n')
