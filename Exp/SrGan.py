"""
Copyright: Intel Corp. 2018
Author: Wenyi Tang
Email: wenyi.tang@intel.com
Created Date: May 17th 2018
Updated Date: May 25th 2018

SRGAN implementation
See https://arxiv.org/abs/1609.04802
"""
from VSR.Framework.SuperResolution import SuperResolution
from VSR.Util.Utility import *

import tensorflow as tf
import numpy as np


class SRGAN(SuperResolution):

    def __init__(self, glayers, dlayers, vgg_layer, init=False, name='srgan', **kwargs):
        self.g_layers = glayers
        self.d_layers = dlayers
        self.vgg_layer = to_list(vgg_layer, 2)
        self.vgg = Vgg(input_shape=[None, None, 3], type='vgg19')
        self.init = init
        self.name = name
        super(SRGAN, self).__init__(**kwargs)

    def build_graph(self):
        with tf.name_scope(self.name):
            super(SRGAN, self).build_graph()
            with tf.name_scope('Generative'):
                shallow_feature = self.conv2d(self.inputs_preproc[-1], 64, 9, activation='relu',
                                              kernel_initializer='he_normal')
                x = shallow_feature
                for _ in range(self.g_layers):
                    x_old = x
                    x = self.conv2d(x, 64, 3, activation='relu', use_batchnorm=True, kernel_initializer='he_normal')
                    x = self.conv2d(x, 64, 3, use_batchnorm=True, kernel_initializer='he_normal')
                    x += x_old
                x = self.conv2d(x, 64, 3, use_batchnorm=True, kernel_initializer='he_normal')
                x += shallow_feature
                x = self.conv2d(x, 256, 3, kernel_initializer='he_normal')
                if self.scale[0] == 4 and self.scale[1] == 4:
                    # the paper fixed scale factor as 4
                    x = self.conv2d(x, 4, 3, activation='relu', kernel_initializer='he_normal')
                    x = pixel_shift(x, 2, 1)
                    x = self.conv2d(x, 256, 3, kernel_initializer='he_normal')
                    x = self.conv2d(x, 4, 3, activation='relu', kernel_initializer='he_normal')
                    x = pixel_shift(x, 2, 1)
                else:
                    x = self.conv2d(x, self.scale[0] * self.scale[1], 3, activation='relu',
                                    kernel_initializer='he_normal')
                    x = pixel_shift(x, self.scale, 1)
                x = self.conv2d(x, 1, 9, kernel_initializer='he_normal')
                self.outputs.append(x)
            _, x = self._build_adversial(x)
            self.outputs.append(x)

    def build_loss(self):
        with tf.name_scope('loss'):
            self.label.append(tf.placeholder(tf.uint8, [None, None, None, 1]))
            y_true = tf.cast(self.label[-1], tf.float32)
            with tf.name_scope('g'):
                mse = tf.losses.mean_squared_error(y_true, self.outputs[0])
                gan_loss = tf.losses.sigmoid_cross_entropy(tf.ones_like(self.outputs[1]), self.outputs[1], weights=1e-3)
                vgg_label = self.vgg(y_true, self.vgg_layer[0], self.vgg_layer[1])
                vgg_sr = self.vgg(self.outputs[0], self.vgg_layer[0], self.vgg_layer[1])
                vgg_loss = tf.losses.mean_squared_error(vgg_label, vgg_sr, weights=2e-6)
                generative_loss = mse + gan_loss + vgg_loss
                g_init_loss = mse
            with tf.name_scope('d'):
                _, reallogit = self._build_adversial(y_true, reuse=True)
                discriminator_loss = tf.losses.sigmoid_cross_entropy(tf.ones_like(reallogit), reallogit) + \
                                     tf.losses.sigmoid_cross_entropy(tf.zeros_like(self.outputs[1]), self.outputs[1])
            update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
            with tf.control_dependencies(update_ops):
                opt_g = tf.train.AdamOptimizer(self.learning_rate)
                opt_d = tf.train.AdamOptimizer(self.learning_rate)
                if self.init:
                    self.loss.append(opt_g.minimize(g_init_loss, self.global_steps))
                else:
                    self.loss.append(opt_g.minimize(generative_loss, self.global_steps))
                    self.loss.append(opt_d.minimize(discriminator_loss, self.global_steps))
            self.metrics['mse'] = mse
            self.metrics['gan_loss'] = gan_loss
            self.metrics['vgg_loss'] = vgg_loss
            self.metrics['d_loss'] = discriminator_loss
            self.metrics['psnr'] = tf.reduce_mean(tf.image.psnr(y_true, self.outputs[0], 255))
            self.metrics['ssim'] = tf.reduce_mean(tf.image.ssim(y_true, self.outputs[0], 255))

    def build_summary(self):
        tf.summary.scalar('loss/mse', self.metrics['mse'])
        tf.summary.scalar('loss/gan', self.metrics['gan_loss'])
        tf.summary.scalar('loss/vgg', self.metrics['vgg_loss'])
        tf.summary.scalar('loss/dis', self.metrics['d_loss'])
        tf.summary.scalar('psnr', self.metrics['psnr'])
        tf.summary.scalar('ssim', self.metrics['ssim'])

    def _build_adversial(self, inputs, reuse=False):
        with tf.variable_scope('Adversarial', reuse=reuse):
            x = self.conv2d(inputs, 64, 3, activation=tf.nn.leaky_relu, kernel_initializer='he_normal')
            filter = 64
            assert self.d_layers % 2 == 0
            strides = [1, 2] * (self.d_layers // 2)
            for i in range(1, self.d_layers):
                filter *= strides[i - 1]
                x = self.conv2d(x, filter, 3, strides=strides[i], activation=tf.nn.leaky_relu, use_batchnorm=True,
                                kernel_initializer='he_normal')
            w_init = tf.initializers.random_normal(stddev=0.02)
            x = tf.layers.dense(x, 1024, tf.nn.leaky_relu, kernel_initializer=w_init)
            x = tf.layers.dense(x, 1, kernel_initializer=w_init)
            return tf.nn.sigmoid(x), x


from VSR.DataLoader.Dataset import load_datasets
from VSR.Framework.Envrionment import Environment
from VSR.Framework.Callbacks import *


def learning_rate_decay(lr, epochs, steps, **kwargs):
    lr = tf.train.exponential_decay(1e-2, global_step=steps, decay_steps=1000, decay_rate=0.96)
    return lr.eval()


if __name__ == '__main__':
    model = SRGAN(scale=4, glayers=16, dlayers=8, vgg_layer=[2, 2], init=True).compile()
    dataset = load_datasets('../Data/datasets.json')['BSD']
    dataset.setattr(patch_size=96, strides=96, random=True, max_patches=64 * 100)
    env = Environment(model, f'../Results/{model.name}/save', f'../Results/{model.name}/log')
    env.fit(64, 1, dataset, learning_rate_schedule=learning_rate_decay, learning_rate=1e-2)
    gan = SRGAN(scale=4, glayers=16, dlayers=8, vgg_layer=[2, 2], init=False).compile()
    env.model = gan
    env.fit(64, 2, dataset, learning_rate_schedule=learning_rate_decay, learning_rate=1e-2)
    env.feature_callbacks = [to_gray()]
    env.label_callbacks = [to_gray()]
    env.output_callbacks += [lambda output, **kwargs: output[0]]
    env.output_callbacks += [to_rgb()]
    env.output_callbacks += [save_image(f'../Results/{model.name}/test')]
    env.test(dataset, convert_to_gray=False)  # load image with 3 channels