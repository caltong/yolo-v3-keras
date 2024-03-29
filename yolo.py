# -*- coding: utf-8 -*-
"""
Class definition of YOLO_v3 style detection model on image and video
"""

import colorsys
from timeit import default_timer as timer

import numpy as np
from keras import backend as K
from keras.models import load_model
from keras.layers import Input
from PIL import Image, ImageFont, ImageDraw

from yolo3.model import yolo_eval, yolo_body, tiny_yolo_body
from yolo3.utils import letterbox_image
import os
from keras.utils import multi_gpu_model


class YOLO(object):
    _defaults = {
        "model_path": 'model_data/yolo.h5',
        "anchors_path": 'model_data/yolo_anchors.txt',
        "classes_path": 'model_data/coco_classes.txt',
        "score": 0.3,
        "iou": 0.45,
        "model_image_size": (416, 416),
        "gpu_num": 1,
    }

    @classmethod
    def get_defaults(cls, n):
        if n in cls._defaults:
            return cls._defaults[n]  # 获取默认值
        else:
            return "Unrecognized attribute name '" + n + "'"  # 若没有指定值 返回信息

    def __init__(self, **kwargs):
        self.__dict__.update(self._defaults)  # set up default values 调用_defaults设置默认值
        self.__dict__.update(kwargs)  # and update with user overrides  使用**kwargs传入任意其他参数
        self.class_names = self._get_class()  # 通过_get_class()来获得classes_path
        self.anchors = self._get_anchors()  # 通过_get_anchors()来获得anchors_path
        self.sess = K.get_session()  # 获取tensorflow的session或者keras的session
        self.boxes, self.scores, self.classes = self.generate()

    def _get_class(self):
        classes_path = os.path.expanduser(self.classes_path)  # 把路径中的~和~user转换成用户目录
        with open(classes_path) as f:
            class_names = f.readlines()
        class_names = [c.strip() for c in class_names]  # 去除首尾空格
        return class_names  # 这里的class为物体识别的种类

    def _get_anchors(self):
        anchors_path = os.path.expanduser(self.anchors_path)  # 把路径中的~和~user转换成用户目录
        with open(anchors_path) as f:
            anchors = f.readline()
        anchors = [float(x) for x in anchors.split(',')]  # 用,分隔数字 转换float
        return np.array(anchors).reshape(-1, 2)  # 转换成np.array 并且reshape成n行2列

    def generate(self):
        model_path = os.path.expanduser(self.model_path)  # 把路径中的~和~user转换成用户目录
        assert model_path.endswith('.h5'), 'Keras model or weights must be a .h5 file.'  # assert必须为.h5文件 否则报错

        # Load model, or construct model and load weights.
        num_anchors = len(self.anchors)  # 获取anchors数量 一个anchors由两个数字组成
        num_classes = len(self.class_names)  # 获取classes数量
        is_tiny_version = num_anchors == 6  # default setting  判断是否为tiny_version 若anchors数量为6 则为tiny_version
        try:
            self.yolo_model = load_model(model_path, compile=False)  # 通过.h5导入模型
        except:
            self.yolo_model = tiny_yolo_body(Input(shape=(None, None, 3)), num_anchors // 2, num_classes) \
                if is_tiny_version else yolo_body(Input(shape=(None, None, 3)), num_anchors // 3, num_classes)
            # 判断是否为tiny_version使用对于函数导入模型
            # TODO: 不知为何yolo_body里面 num_anchors//3 不是//2 这里的不同会导致下面assert判断中model.layers[-1].output_shape[-1]不同
            self.yolo_model.load_weights(self.model_path)  # make sure model, anchors and classes match
            # 使用load_weights导入权重
        else:
            assert self.yolo_model.layers[-1].output_shape[-1] == \
                   num_anchors / len(self.yolo_model.output) * (num_classes + 5), \
                'Mismatch between model and given anchor and class sizes'
            # assert判断model和给定的anchor class数量是否一致
        print('{} model, anchors, and classes loaded.'.format(model_path))  # 输出model_path

        # Generate colors for drawing bounding boxes.
        # 生产目标检测框的颜色
        hsv_tuples = [(x / len(self.class_names), 1., 1.)
                      for x in range(len(self.class_names))]
        self.colors = list(map(lambda x: colorsys.hsv_to_rgb(*x), hsv_tuples))  # 通过colorsys.hsv_to_rgb产生多种颜色
        self.colors = list(
            map(lambda x: (int(x[0] * 255), int(x[1] * 255), int(x[2] * 255)),
                self.colors))  # 0-1转换成0-255
        np.random.seed(10101)  # Fixed seed for consistent colors across runs. 随机种子10101
        np.random.shuffle(self.colors)  # Shuffle colors to decorrelate adjacent classes. 随机打乱之前生成的颜色
        np.random.seed(None)  # Reset seed to default. 重置随机种子

        # Generate output tensor targets for filtered bounding boxes.
        self.input_image_shape = K.placeholder(shape=(2,))  # 定义input_image_shape为placeholder
        if self.gpu_num >= 2:
            self.yolo_model = multi_gpu_model(self.yolo_model, gpus=self.gpu_num)  # 如果GPU数量大于等于2启用多GPU模型
        boxes, scores, classes = yolo_eval(self.yolo_model.output, self.anchors,
                                           len(self.class_names), self.input_image_shape,
                                           score_threshold=self.score, iou_threshold=self.iou)
        # yolo3.model.yolo_eval函数
        return boxes, scores, classes

    def detect_image(self, image):
        start = timer()  # 开始计时

        if self.model_image_size != (None, None):
            assert self.model_image_size[0] % 32 == 0, 'Multiples of 32 required'
            assert self.model_image_size[1] % 32 == 0, 'Multiples of 32 required'
            boxed_image = letterbox_image(image,
                                          tuple(reversed(self.model_image_size)))  # yolo3.utils.letterbox_image压缩图片
        else:
            new_image_size = (image.width - (image.width % 32),
                              image.height - (image.height % 32))
            boxed_image = letterbox_image(image, new_image_size)
        image_data = np.array(boxed_image, dtype='float32')  # 转换np.array

        print(image_data.shape)
        image_data /= 255.  # 转换成0-1
        image_data = np.expand_dims(image_data, 0)  # Add batch dimension. expand_dims添加一个维度 axis=0 添加第一个维度

        out_boxes, out_scores, out_classes = self.sess.run(
            [self.boxes, self.scores, self.classes],
            feed_dict={
                self.yolo_model.input: image_data,
                self.input_image_shape: [image.size[1], image.size[0]],
                K.learning_phase(): 0
            })  # 预测 获取框选 概率 类别

        print('Found {} boxes for {}'.format(len(out_boxes), 'img'))  # 打印出框选

        font = ImageFont.truetype(font='font/FiraMono-Medium.otf',
                                  size=np.floor(3e-2 * image.size[1] + 0.5).astype('int32'))  # 字体字号设置
        thickness = (image.size[0] + image.size[1]) // 300  # 粗细

        for i, c in reversed(list(enumerate(out_classes))):  # 遍历所有识别出的物体
            predicted_class = self.class_names[c]  # 预测类别
            box = out_boxes[i]  # 预测框选
            score = out_scores[i]  # 预测概率

            label = '{} {:.2f}'.format(predicted_class, score)  # 标签
            draw = ImageDraw.Draw(image)  # 画
            label_size = draw.textsize(label, font)  # 标签尺寸

            top, left, bottom, right = box
            top = max(0, np.floor(top + 0.5).astype('int32'))
            left = max(0, np.floor(left + 0.5).astype('int32'))
            bottom = min(image.size[1], np.floor(bottom + 0.5).astype('int32'))
            right = min(image.size[0], np.floor(right + 0.5).astype('int32'))
            print(label, (left, top), (right, bottom))

            if top - label_size[1] >= 0:
                text_origin = np.array([left, top - label_size[1]])
            else:
                text_origin = np.array([left, top + 1])

            # My kingdom for a good redistributable image drawing library.
            for i in range(thickness):
                draw.rectangle(
                    [left + i, top + i, right - i, bottom - i],
                    outline=self.colors[c])
            draw.rectangle(
                [tuple(text_origin), tuple(text_origin + label_size)],
                fill=self.colors[c])
            draw.text(text_origin, label, fill=(0, 0, 0), font=font)
            del draw

        end = timer()  # 结束计时
        print(end - start)  # 检测图片用时
        return image

    def close_session(self):
        self.sess.close()


def detect_video(yolo, video_path, output_path=""):
    import cv2
    vid = cv2.VideoCapture(video_path)
    if not vid.isOpened():
        raise IOError("Couldn't open webcam or video")
    video_FourCC = int(vid.get(cv2.CAP_PROP_FOURCC))
    video_fps = vid.get(cv2.CAP_PROP_FPS)
    video_size = (int(vid.get(cv2.CAP_PROP_FRAME_WIDTH)),
                  int(vid.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    isOutput = True if output_path != "" else False
    if isOutput:
        print("!!! TYPE:", type(output_path), type(video_FourCC), type(video_fps), type(video_size))
        out = cv2.VideoWriter(output_path, video_FourCC, video_fps, video_size)
    accum_time = 0
    curr_fps = 0
    fps = "FPS: ??"
    prev_time = timer()
    while True:
        return_value, frame = vid.read()
        image = Image.fromarray(frame)
        image = yolo.detect_image(image)
        result = np.asarray(image)
        curr_time = timer()
        exec_time = curr_time - prev_time
        prev_time = curr_time
        accum_time = accum_time + exec_time
        curr_fps = curr_fps + 1
        if accum_time > 1:
            accum_time = accum_time - 1
            fps = "FPS: " + str(curr_fps)
            curr_fps = 0
        cv2.putText(result, text=fps, org=(3, 15), fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                    fontScale=0.50, color=(255, 0, 0), thickness=2)
        cv2.namedWindow("result", cv2.WINDOW_NORMAL)
        cv2.imshow("result", result)
        if isOutput:
            out.write(result)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    yolo.close_session()
