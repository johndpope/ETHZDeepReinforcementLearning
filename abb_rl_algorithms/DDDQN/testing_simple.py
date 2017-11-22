import tensorflow as tf
import os, csv
import datetime as dt
from network import Qnetwork
from environment import Environment
from experience_replay_buffer import ExperienceReplayBuffer
import numpy as np
import time
import rl_logging
import collections
slim = tf.contrib.slim
from scipy import misc
import pandas as pd
import pickle
import matplotlib.pyplot as plt
import random


FLAGS = tf.app.flags.FLAGS

################################
# General
################################


tf.app.flags.DEFINE_string(
    'description', 'divide input irr and control by 1000 to get equal scal, compare with normal h1 lr 0.00025 guided30 ',
    'decribe experiment')


tf.app.flags.DEFINE_integer(
    'seed', 1337 ,
    'seed')

seed = FLAGS.seed
np.random.seed(seed)  # Reproducibility
random.seed(seed)



tf.app.flags.DEFINE_float(
    'per_process_gpu_memory_fraction', 0.05,
    'fraction of gpu memory used for this process')
# run_simple_irr_dqn_hl_mse_tau_0001_e_200_followirr_randomexp_sunspot_action_naive_nopt_mpc_divi_mask_uf1/
tf.app.flags.DEFINE_string(
    'train_dir',
    '/home/dladmin/Documents/arthurma/experiments_rl_final/action_space_1/guided_normalized_clipped_input/mpc30_lr00025_tau10e3_div1000_clip600/',
    'Directory where checkpoints, info, and event logs are written to.')

tf.app.flags.DEFINE_string(
    'pretrained_checkpoint_path'
    , None
    , 'The path to a checkpoint from which to fine-tune. Only if restore_latest_checkpoint is false')

tf.app.flags.DEFINE_boolean(
    'use_restore_dict',
    False,
    'For partial restore,update dictionary in network.py')

tf.app.flags.DEFINE_string(
    'train_set_path',
    '/home/dladmin/Documents/arthurma/shared/dlabb/abb_rl_algorithms/DDDQN/train_list.out',
    'Directory where File with train set is')

tf.app.flags.DEFINE_integer(
    'sample_train_episodes',
    None,
    'Randomly sample a subset of training episodes')

tf.app.flags.DEFINE_string(
    'test_set_path',
    '/home/dladmin/Documents/arthurma/shared/dlabb/abb_rl_algorithms/DDDQN/validation_list.out',
    'Directory where File with test set is')

# /home/dladmin/Documents/arthurma/rf/eval/run_hl_mse_tau_00001_e_100_followirr_guidedexp_sunspot_per_50442_TRAIN/low_reward_episodes.pickle
# '/home/dladmin/Documents/arthurma/rf/low_reward_episodes200.pickle'
tf.app.flags.DEFINE_string(
    'load_train_episodes'
    ,None,
    'Directory where File with test set is')

# "/home/dladmin/Documents/arthurma/rf/low_reward_episodes200.pickle"

tf.app.flags.DEFINE_string(
    'load_test_episodes',
    None,
    'Directory where File with test set is')

tf.app.flags.DEFINE_integer(
    'episode_length_train', None,
    'Length, None for full day')

tf.app.flags.DEFINE_integer(
    'episode_length_test', None,
    'Length, None for full day')

tf.app.flags.DEFINE_string(
    'data_file',
    'rl_data_sp.csv',
    'rl_data_sp.csv for images with green spot over sun')

################################
# Input Data
################################
tf.app.flags.DEFINE_integer(
    'batch_size', 32,
    'samples from experience replay, randomly sampled')

tf.app.flags.DEFINE_integer(
    'img_size', 84,
    'height/width of input image')

tf.app.flags.DEFINE_integer(
    'img_sequence_length', 2,
    'nr of image in one sequence')

tf.app.flags.DEFINE_integer(
    'img_sequence_stride', 9,
    'nr of image in one sequence')

tf.app.flags.DEFINE_float(
    'divide_image_values', 255.0,
    '255.0 or 1.0 or None (255 -> values between 0 and 1) Note: converts values to float32, increases memory usage by 4')

tf.app.flags.DEFINE_float(
    'divide_irr_ci', 1000.0,
    '255.0 or 1.0 or None (255 -> values between 0 and 1) Note: converts values to float32, increases memory usage by 4')


tf.app.flags.DEFINE_string(
    'mask', '/media/data/Daten/img_C/cavriglia_skymask256.png',
    'Mask applied to images')


################################
# Simple QNetwork
################################

tf.app.flags.DEFINE_string(
    'network', "simple_duelling_dqn",
    'simple_irr_dqn, simple_duelling_dqn')

tf.app.flags.DEFINE_boolean(
    'add_irr', True,
    'Add current irradiance to the model')

tf.app.flags.DEFINE_float(
    'stream_hidden_layer_size', 256,
    'hidden layer size before Q-value regression')

tf.app.flags.DEFINE_boolean(
    'duelling', True,
    'Duelling or not')

################################
# Action Space
################################
tf.app.flags.DEFINE_integer(
    'num_actions', 7,
    'Nr of actions to choose from')

# Enforces ramp restrictions: 100.0: 10% per minute => environment calculates depending on the time change between states which is roughly 6-8 seconds
tf.app.flags.DEFINE_float(
    'max_ramp_change_per_minute', 100.0,
    'Assuming 3 actions: Stay put, change battery throughput up by max_ramp_change or down by -max_ramp_change')

tf.app.flags.DEFINE_integer(
    'action_space', 1,
    '0: three action simple, 1: 0 follows irr, 7 actions, 2 0 follows irr only if irr is reachable otherwise straight, 7 actions')

tf.app.flags.DEFINE_integer(
    'reward_type', 1,
    '0: negative difference control/irr, 2: 1/abs(irr-input) positive between 0 and 1')


################################
# Logging and Checkpoints
################################

tf.app.flags.DEFINE_float(
    'low_reward_t', -100,
    'threshold for logging low reward episodes (high error)')

tf.app.flags.DEFINE_string(
    'low_reward_output_name', 'low_reward_episodes200.pickle',
    'threshold for logging low reward episodes (high error)')


tf.app.flags.DEFINE_integer(
    'log_every_n_steps', 20,
    'The frequency with which logs are print.')


tf.app.flags.DEFINE_boolean(
    'render_ep', False,
    'Render current train episode')



######################################################################################
##EPISODE RENDER
######################################################################################

def render_episode(current_control_input, current_episode):
    irr_list, mpc_list, ci_list, index_list = [], [], [], []
    # column_list = ["irr", "mpc", "ci"]

    for t in current_control_input:
        index_list.append(t[1])
        ci_list.append(t[0])

    ci_df = pd.DataFrame(data=ci_list, index=index_list, columns=["ci"])

    irrmpc_df = current_episode.loc[ci_df.index]

    data_df = pd.concat([ci_df, irrmpc_df], axis=1)

    data_df[["ci", "irr", "mpc"]].plot()

    plt.show()

######################################################################################
##TESTING
######################################################################################

def do_validation_run(sess, network, env, train_writer, step, output_path):
    nr_validation_episodes = env.nr_test_episodes
    print("Validation run on " + str(nr_validation_episodes) + " episodes...")

    total_episodes_reward_list = list()
    total_episodes_q_value_list = list()
    total_chosen_action_list = list()
    control_input_df_list = list()
    low_reward_episodes = list()

    for episode_nr in range(nr_validation_episodes):
        print("Validation Episode " + str(episode_nr + 1) + "/" + str(nr_validation_episodes))

        state, abort = env.test_reset()  # get next episode, initialize, TEST SET
        done = 0

        episode_reward_sum = 0
        episode_q_value_sum = 0
        episode_steps = 0
        episode_chosen_action_list = list()
        episode_reward_list = list()
        episode_q_value_list = list()

        while done == 0 and not abort:
            episode_steps += 1

            action, action_value_qs = \
                sess.run([network.predict, network.Qout],
                         feed_dict={network.input_image_sequence: (np.array([state[0]]))*(1/FLAGS.divide_image_values),
                                    network.input_current_irradiance: np.reshape(
                                        [state[1]],
                                        [-1, FLAGS.img_sequence_length]) *(1/FLAGS.divide_irr_ci),
                                    network.input_current_control_input: np.reshape(
                                        [state[2]],
                                        [-1, 1])*(1/FLAGS.divide_irr_ci),network.keep_prob:1.0})

            # TODO: extract V , Advantage  of current state as well.. maybe also directly TSNE embedding on some of the inputs.. save coordinates in csv

            mean_max_action_value_q = np.mean(np.max(action_value_qs, axis=1))

            next_state, reward, done = env.test_step(action) #step TEST SET

            episode_reward_sum += reward
            episode_q_value_sum += mean_max_action_value_q

            episode_reward_list.append(reward)
            episode_q_value_list.append(mean_max_action_value_q)
            episode_chosen_action_list.append(action[0])

            total_chosen_action_list.append(action[0])

            state = next_state


        #Episode Done
        if episode_steps>0:
            print("Cumulative episode " + str(episode_nr + 1) + " reward:", episode_reward_sum)
            print("Average episode " + str(episode_nr + 1) + " reward:", episode_reward_sum / episode_steps)
            print("Average episode " + str(episode_nr + 1) + " Q Value:", episode_q_value_sum / episode_steps)
            total_episodes_reward_list.append(episode_reward_sum / episode_steps)
            total_episodes_q_value_list.append(episode_q_value_sum / episode_steps)


            if FLAGS.low_reward_t is not None:
                if episode_reward_sum < FLAGS.low_reward_t:
                    print("Adding episode to low reward list...")
                    low_reward_episodes.append(env.current_testing_episode)


            episode_action_counter = collections.Counter(episode_chosen_action_list)
            rl_logging.save_statistics(train_writer=train_writer, episodes_reward_list=episode_reward_list,
                                       episodes_mean_max_q_value_list=episode_q_value_list,episodes_mean_chosen_q_value_list=None, episodes_mean_batch_reward_list=None, step=episode_nr,
                                       action_counter=episode_action_counter,
                                       set="validation_episode")

            # Calculate control input dataframe, interpolate linearly to seconds

            control_inputs_tuple = network.env.current_episode_test_control_input_values

            c_inputs = [t[0] for t in control_inputs_tuple]
            c_index = [t[1] for t in control_inputs_tuple]

            ci_df = pd.DataFrame(data=c_inputs, index=c_index, columns=["ci"])
            ci_df = ci_df.asfreq('S')
            ci_df = ci_df.astype(float).interpolate(method='time')


            control_input_df_list.append(ci_df)

            if FLAGS.render_ep:
                render_episode(env.current_test_control_inputs, env.current_test_episode)

    action_counter = collections.Counter(total_chosen_action_list)

    total_control_input_df = pd.concat(control_input_df_list, axis=0).sort_index()

    total_control_input_df.to_csv(os.path.join(output_path, "eval_predictions.csv"))

    rl_logging.save_statistics(train_writer=train_writer, episodes_reward_list=total_episodes_reward_list,
                               episodes_mean_max_q_value_list=total_episodes_q_value_list,episodes_mean_chosen_q_value_list=None, episodes_mean_batch_reward_list=None, step=step,
                               action_counter=action_counter,
                               set="validation_epoch", write_path=output_path)


    if low_reward_episodes and FLAGS.low_reward_t:
        print("Creating serialized low reward episodes list...")
        lr_path = os.path.join(FLAGS.eval_dir,FLAGS.low_reward_output_name)
        with open(lr_path,'wb') as f:
            pickle.dump(low_reward_episodes,f)


#######################################################################################################################
##MAIN
#######################################################################################################################



def main(_):
    if tf.gfile.Exists(FLAGS.eval_dir):
        raise ValueError("Eval directory exists already")

    os.makedirs(FLAGS.eval_dir)

    with open(os.path.join(FLAGS.eval_dir, "test_model_info.csv"), "w+") as f:
        w = csv.writer(f)
        w.writerow([str(dt.datetime.now())])
        for key, val in FLAGS.__flags.items():
            w.writerow([key, val])

    ###################
    # Initialize
    ###################

    with tf.device('/gpu:0'):

        config = tf.ConfigProto(
            device_count={'GPU': 1},
            gpu_options=tf.GPUOptions(per_process_gpu_memory_fraction=0.1)
        )

        tf.logging.set_verbosity(tf.logging.INFO)
        tf.reset_default_graph()
        tf.set_random_seed(seed)

        # Environment and Networks
        env = Environment(train_set_path=FLAGS.train_set_path, test_set_path=FLAGS.test_set_path,
                          episode_length_train=FLAGS.episode_length_train,
                          episode_length_test=FLAGS.episode_length_test,
                          sequence_length=FLAGS.img_sequence_length, sequence_stride=FLAGS.img_sequence_stride,
                          actions=FLAGS.num_actions,
                          image_size=FLAGS.img_size, action_space=FLAGS.action_space, file=FLAGS.data_file,
                          load_train_episodes=FLAGS.load_train_episodes, load_test_episodes=FLAGS.load_test_episodes,
                          mask_path=FLAGS.mask,
                          divide_image_values=FLAGS.divide_image_values,
                          sample_training_episodes=FLAGS.sample_train_episodes,
                          exploration_follow=FLAGS.exploration_follow, start_exploration_deviation=FLAGS.start_exploration_deviation,
                          reward_type = FLAGS.reward_type)

        mainQN = Qnetwork(environment=env, stream_hidden_layer_size=FLAGS.stream_hidden_layer_size,
                          img_size=FLAGS.img_size,
                          img_sequence_len=FLAGS.img_sequence_length, huber_delta=FLAGS.huber_delta,
                          adam_epsilon=FLAGS.adam_epsilon, add_irr=FLAGS.add_irr, train_value_only=FLAGS.train_value_only,duelling=FLAGS.duelling,gradient_clipping=FLAGS.gradient_clipping,optimizer=FLAGS.optimizer)

        targetQN = Qnetwork(environment=env, stream_hidden_layer_size=FLAGS.stream_hidden_layer_size,
                            img_size=FLAGS.img_size,
                            img_sequence_len=FLAGS.img_sequence_length, huber_delta=FLAGS.huber_delta,
                            adam_epsilon=FLAGS.adam_epsilon, add_irr=FLAGS.add_irr, train_value_only=FLAGS.train_value_only,duelling=FLAGS.duelling,gradient_clipping=FLAGS.gradient_clipping,optimizer=FLAGS.optimizer)

        if FLAGS.network == "simple_duelling_dqn":
            mainQN.simple_duelling_dqn(regularizer=FLAGS.l2_regularizer, scope='simple_duelling_dqn_main')
            targetQN.simple_duelling_dqn(regularizer=FLAGS.l2_regularizer, scope='simple_duelling_dqn_target')

        elif FLAGS.network == "simple_duelling_dqn_old":
            mainQN.simple_duelling_dqn_old(regularizer=FLAGS.l2_regularizer, scope='simple_duelling_dqn_main')
            targetQN.simple_duelling_dqn_old(regularizer=FLAGS.l2_regularizer, scope='simple_duelling_dqn_target')
        else:
            raise ValueError("Illegal architecture")


        ###################
        # TESTING SESSION
        ###################
        saver = tf.train.Saver()
        init = tf.global_variables_initializer()

        with tf.Session(config=config) as sess:
            sess.run(init)

            print('Loading pretrained model...')
            saver.restore(sess, FLAGS.pretrained_checkpoint_path)

            # Tensorboard summary writer
            train_writer = tf.summary.FileWriter(FLAGS.eval_dir,
                                                 sess.graph)

            do_validation_run(train_writer=train_writer, sess=sess, network=mainQN, env=env, step=1,
                              output_path=FLAGS.eval_dir)


#######################################################################################################################
if __name__ == '__main__':
    print("Start")
    tf.app.run()
