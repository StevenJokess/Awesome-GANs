from __future__ import absolute_import
from __future__ import print_function
from __future__ import division

import tensorflow as tf
import numpy as np

import sys
import time

import began_model as began

sys.path.append('../')
import image_utils as iu
from datasets import DataIterator
from datasets import CelebADataSet as DataSet


results = {
    'output': './gen_img/',
    'checkpoint': './model/checkpoint',
    'model': './model/BEGAN-model.ckpt'
}

train_step = {
    'epoch': 64,
    'batch_size': 64,
    'logging_step': 2500,
}


def main():
    start_time = time.time()  # Clocking start

    # GPU configure
    config = tf.ConfigProto()
    config.gpu_options.allow_growth = True

    with tf.Session(config=config) as s:
        # BEGAN Model
        model = began.BEGAN(s)  # BEGAN

        # Initializing
        s.run(tf.global_variables_initializer())

        # Celeb-A DataSet images
        ds = DataSet(input_height=32,
                     input_width=32,
                     input_channel=3,
                     mode='r').images
        dataset_iter = DataIterator(ds, None, train_step['batch_size'],
                                    label_off=True)

        sample_x = ds[:model.sample_num]
        sample_x = np.reshape(sample_x, [-1] + model.image_shape[1:])
        sample_z = np.random.uniform(-1., 1., [model.sample_num, model.z_dim]).astype(np.float32)

        # Export real image
        valid_image_height = model.sample_size
        valid_image_width = model.sample_size
        sample_dir = results['output'] + 'valid.png'

        # Generated image save
        iu.save_images(sample_x, size=[valid_image_height, valid_image_width], image_path=sample_dir)

        global_step = 0
        for epoch in range(train_step['epoch']):
            for batch_images in dataset_iter.iterate():
                batch_x = np.reshape(batch_images, [-1] + model.image_shape[1:])
                batch_z = np.random.uniform(-1., 1., [model.batch_size, model.z_dim]).astype(np.float32)

                # Update D network
                _, d_loss = s.run([model.d_op, model.d_loss],
                                  feed_dict={
                                      model.x: batch_x,
                                      model.z: batch_z,
                                  })

                # Update G network
                _, g_loss = s.run([model.g_op, model.g_loss],
                                  feed_dict={
                                      model.z: batch_z,
                                  })

                # Update k_t
                _, k, m_global = s.run([model.k_update, model.k, model.m_global],
                                       feed_dict={
                                            model.x: batch_x,
                                            model.z: batch_z,
                                       })

                if global_step % train_step['logging_step'] == 0:
                    batch_z = np.random.uniform(-1., 1., [model.batch_size, model.z_dim]).astype(np.float32)

                    # Summary
                    _, k, m_global, d_loss, g_loss, summary = s.run([model.k_update, model.k, model.m_global,
                                                                     model.d_loss, model.g_loss, model.merged],
                                                                    feed_dict={
                                                                        model.x: batch_x,
                                                                        model.z: batch_z,
                                                                    })

                    # Print loss
                    print("[+] Epoch %04d Step %07d =>" % (epoch, global_step),
                          " D loss : {:.8f}".format(d_loss),
                          " G loss : {:.8f}".format(g_loss),
                          " k : {:.8f}".format(k),
                          " M : {:.8f}".format(m_global))

                    # Summary saver
                    model.writer.add_summary(summary, epoch)

                    # Training G model with sample image and noise
                    samples = s.run(model.g,
                                    feed_dict={
                                        model.x: sample_x,
                                        model.z: sample_z,
                                    })

                    # Export image generated by model G
                    sample_image_height = model.sample_size
                    sample_image_width = model.sample_size
                    sample_dir = results['output'] + 'train_{0}_{1}.png'.format(epoch, global_step)

                    # Generated image save
                    iu.save_images(samples,
                                   size=[sample_image_height, sample_image_width],
                                   image_path=sample_dir)

                    # Model save
                    model.saver.save(s, results['model'], global_step=global_step)

                global_step += 1

    end_time = time.time() - start_time  # Clocking end

    # Elapsed time
    print("[+] Elapsed time {:.8f}s".format(end_time))

    # Close tf.Session
    s.close()


if __name__ == '__main__':
    main()
