import tensorflow as tf
import numpy as np

import sys

sys.path.append('../')
import tfutil as t

np.random.seed(777)
tf.set_random_seed(777)  # reproducibility


class BigGAN:

    def __init__(self, s, batch_size=64, height=128, width=128, channel=3,
                 sample_num=10 * 10, sample_size=10,
                 df_dim=64, gf_dim=64, fc_unit=512, z_dim=128, lr=1e-4):

        """
        # General Settings
        :param s: TF Session
        :param batch_size: training batch size, default 64
        :param height: image height, default 64
        :param width: image width, default 64
        :param channel: image channel, default 3

        # Output Settings
        :param sample_num: the number of output images, default 100
        :param sample_size: sample image size, default 10

        # For Model
        :param df_dim: discriminator conv filter, default 64
        :param gf_dim: generator conv filter, default 64
        :param fc_unit: the number of fully connected layer units, default 512

        # Training Option
        :param z_dim: z dimension (kinda noise), default 128
        :param lr: learning rate, default 1e-4
        """

        self.s = s
        self.batch_size = batch_size

        self.height = height
        self.width = width
        self.channel = channel
        self.image_shape = [self.batch_size, self.height, self.width, self.channel]

        assert self.height == self.width
        assert self.height in [128, 256, 512]

        self.sample_num = sample_num
        self.sample_size = sample_size

        self.df_dim = df_dim
        self.gf_dim = gf_dim
        self.fc_unit = fc_unit

        self.up_sampling = True

        self.z_dim = z_dim
        self.beta1 = 0.9
        self.beta2 = .999
        self.lr = lr

        self.res_block_disc = None
        self.res_block_gen = None
        if self.height == 128:
            self.res_block_disc = ([16, 8, 4, 2], [1])
            self.res_block_gen = ([1], [2, 4, 8, 16, 16])
        elif self.height == 256:
            self.res_block_disc = ([16, 8, 8, 4, 2], [1])
            self.res_block_gen = ([1, 2], [4, 8, 8, 16, 16])
        elif self.height == 512:
            self.res_block_disc = ([16, 8, 8, 4], [2, 1, 1])
            self.res_block_gen = ([1, 1, 2], [4, 8, 8, 16, 16])
        else:
            raise NotImplementedError

        # pre-defined
        self.g_loss = 0.
        self.d_loss = 0.
        self.c_loss = 0.

        self.g = None

        self.d_op = None
        self.g_op = None

        self.merged = None
        self.writer = None
        self.saver = None

        # Placeholders
        self.x = tf.placeholder(tf.float32,
                                shape=[None, self.height, self.width, self.channel],
                                name="x-image")  # (64, 64, 64, 3)
        self.z = tf.placeholder(tf.float32, shape=[None, self.z_dim], name="z-noise")  # (-1, 128)

        self.build_sagan()  # build SAGAN model

    @staticmethod
    def res_block_up(x, c, f, name):
        with tf.variable_scope("res_block_up-%s" % name):
            ssc = x

            x = t.batch_norm(x, name="bn-1")  # <- noise
            x = tf.nn.relu(x)
            x = t.conv2d(x, f, name="conv2d-1")

            x = t.batch_norm(x, name="bn-2")  # <- noise
            x = tf.nn.relu(x)
            x = t.conv2d(x, f, name="conv2d-2")
            return x + ssc

    def res_block_down(self, x, c, f, name):
        pass

    @staticmethod
    def non_local_block(x, f, sub_sampling=False, name="nonlocal"):
        """ non-local block, https://arxiv.org/pdf/1711.07971.pdf """
        with tf.variable_scope("non_local_block-%s" % name):
            theta = t.conv2d(x, f=f, k=1, s=1, name="theta")
            if sub_sampling:
                theta = tf.layers.max_pooling2d(theta, pool_size=(2, 2), name="max_pool-theta")
            theta = tf.reshape(theta, (-1, theta.get_shape().as_list()[-1]))

            phi = t.conv2d(x, f=f, k=1, s=1, name="phi")
            if sub_sampling:
                phi = tf.layers.max_pooling2d(theta, pool_size=(2, 2), name="max_pool-phi")
            phi = tf.reshape(phi, (-1, phi.get_shape().as_list()[-1]))
            phi = tf.transpose(phi, [1, 0])

            g = t.conv2d(x, f=f, k=1, s=1, name="g")
            if sub_sampling:
                g = tf.layers.max_pooling2d(theta, pool_size=(2, 2), name="max_pool-g")
            g = tf.reshape(g, (-1, g.get_shape().as_list()[-1]))

            theta_phi = tf.multiply(theta, phi)
            theta_phi = tf.nn.softmax(theta_phi)

            theta_phi_g = tf.multiply(theta_phi, g)

            theta_phi_g = t.conv2d(theta_phi_g, f=f, k=1, s=1, name="theta_phi_g")
            return x + theta_phi_g

    def discriminator(self, x, reuse=None):
        """
        :param x: images
        :param y: labels
        :param reuse: re-usable
        :return: classification, probability (fake or real), network
        """
        with tf.variable_scope("discriminator", reuse=reuse):
            f = self.gf_dim

            x = t.conv2d_alt(x, f, 4, 2, pad=1, sn=True, name='disc-conv2d-1')
            x = tf.nn.leaky_relu(x, alpha=0.1)

            for i in range(self.n_layer // 2):
                x = t.conv2d_alt(x, f * 2, 4, 2, pad=1, sn=True, name='disc-conv2d-%d' % (i + 2))
                x = tf.nn.leaky_relu(x, alpha=0.1)

                f *= 2

            # Self-Attention Layer
            x = self.attention(x, f, reuse=reuse)

            for i in range(self.n_layer // 2, self.n_layer):
                x = t.conv2d_alt(x, f * 2, 4, 2, pad=1, sn=True, name='disc-conv2d-%d' % (i + 2))
                x = tf.nn.leaky_relu(x, alpha=0.1)

                f *= 2

            x = t.flatten(x)

            x = t.dense_alt(x, 1, sn=True, name='disc-fc-1')
            return x

    def generator(self, z, c, reuse=None):
        """
        :param z: noise
        :param c: image label
        :param reuse: re-usable
        :return: prob
        """
        with tf.variable_scope("generator", reuse=reuse):
            # split
            z = tf.split(z, num_or_size_splits=4, axis=-1)  # expected [None, 32] * 4

            # linear projection
            x = t.dense(z, f=4 * 4 * 16 * self.channel, name="disc-dense-1")
            x = tf.nn.relu(x)

            res = x
            for i in range(4):
                res = self.res_block_up(res, f=(16 // (2 ** i)) * self.channel, name="res%d" % (i + 1))
                res = tf.concat([res, z], axis=-1)

            x = res  # To-Do : non-local block

            x = self.res_block_up(x, f=self.channel, name="res4")

            x = t.batch_norm(x, name="bn-last")  # <- noise
            x = tf.nn.relu(x)
            x = t.conv2d(x, f=3, k=3, name="conv2d-last")

            x = tf.nn.tanh(x)
            return x

    def build_sagan(self):
        # Generator
        self.g = self.generator(self.z)

        # Discriminator
        d_real = self.discriminator(self.x)
        d_fake = self.discriminator(self.g, reuse=True)

        # Losses
        d_real_loss = t.sce_loss(d_real, tf.ones_like(d_real))
        d_fake_loss = t.sce_loss(d_fake, tf.zeros_like(d_fake))
        self.d_loss = d_real_loss + d_fake_loss
        self.g_loss = t.sce_loss(d_fake, tf.ones_like(d_fake))

        # Summary
        tf.summary.scalar("loss/d_real_loss", d_real_loss)
        tf.summary.scalar("loss/d_fake_loss", d_fake_loss)
        tf.summary.scalar("loss/d_loss", self.d_loss)
        tf.summary.scalar("loss/g_loss", self.g_loss)

        # Optimizer
        t_vars = tf.trainable_variables()
        d_params = [v for v in t_vars if v.name.startswith('d')]
        g_params = [v for v in t_vars if v.name.startswith('g')]

        self.d_op = tf.train.AdamOptimizer(self.lr * 4,
                                           beta1=self.beta1, beta2=self.beta2).minimize(self.d_loss, var_list=d_params)
        self.g_op = tf.train.AdamOptimizer(self.lr * 1,
                                           beta1=self.beta1, beta2=self.beta2).minimize(self.g_loss, var_list=g_params)

        # Merge summary
        self.merged = tf.summary.merge_all()

        # Model saver
        self.saver = tf.train.Saver(max_to_keep=1)
        self.writer = tf.summary.FileWriter('./model/', self.s.graph)