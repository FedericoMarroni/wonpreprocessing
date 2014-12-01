#!/usr/bin/env python

__author__ = 'hfriedrich'

import logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)-8s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S')
_log = logging.getLogger()

import os
import codecs
import argparse
import numpy as np
from scipy.sparse import csr_matrix, lil_matrix
import sklearn.metrics as m
from time import strftime
from tools.cosine_link_prediction import cosinus_link_prediciton
from tools.tensor_utils import CONNECTION_SLICE, NEED_TYPE_SLICE, connection_indices, read_input_tensor, need_indices, want_indices, offer_indices, \
    predict_rescal_connections_by_need_similarity, predict_rescal_connections_by_threshold, similarity_ranking, \
    matrix_to_array, execute_rescal, predict_rescal_connections_array, attribute_indices

# for all test_needs return all indices (shuffeld) to all other needs in the connection slice
def need_connection_indices(all_needs, test_needs):
    allindices = ([],[])
    for row in test_needs:
        fromneeds = [row] * len(all_needs)
        toneeds = all_needs
        allindices[0].extend(fromneeds)
        allindices[1].extend(toneeds)
    indices = range(len(allindices[0]))
    np.random.shuffle(indices)
    ret0 = [allindices[0][i] for i in indices]
    ret1 = [allindices[1][i] for i in indices]
    return (ret0, ret1)

# mask all connections at specified indices in the tensor
def mask_idx_connections(tensor, indices):
    slices = [lil_matrix(slice.copy()) for slice in tensor]
    for idx in range(len(indices[0])):
        slices[CONNECTION_SLICE][indices[0][idx],indices[1][idx]] = 0
        slices[CONNECTION_SLICE][indices[1][idx],indices[0][idx]] = 0
    Tc = [csr_matrix(slice) for slice in slices]
    return Tc

# mask all connections of some needs to all other needs
def mask_need_connections(tensor, needs):
    slices = [lil_matrix(slice.copy()) for slice in tensor]
    for need in needs:
        slices[CONNECTION_SLICE][need,:] = lil_matrix(np.zeros(tensor[CONNECTION_SLICE].shape[0]))
        slices[CONNECTION_SLICE][:,need] = lil_matrix(np.zeros(tensor[CONNECTION_SLICE].shape[0])).transpose()
    Tc = [csr_matrix(slice) for slice in slices]
    return Tc

# mask all connections but a number of X for each need
def mask_all_but_X_connections_per_need(tensor, keep_x):
    slices = [lil_matrix(slice.copy()) for slice in tensor]
    for row in set(tensor[CONNECTION_SLICE].nonzero()[0]):
        if slices[CONNECTION_SLICE][row,:].getnnz() > keep_x:
            mask_idx = slices[CONNECTION_SLICE].nonzero()[1][np.where(slices[CONNECTION_SLICE].nonzero()[0]==row)]
            np.random.shuffle(mask_idx)
            for col in mask_idx[keep_x:]:
                slices[CONNECTION_SLICE][row,col] = 0
                slices[CONNECTION_SLICE][col,row] = 0
    Tc = [csr_matrix(slice) for slice in slices]
    return Tc

# choose number of x needs to keep and remove all other needs (including references to attrubutes, connections, etc)
# that exceed this number
def keep_x_random_needs(tensor, headers, keep_x):
    rand_needs = need_indices(headers)
    np.random.shuffle(rand_needs)
    remove_needs = rand_needs[keep_x:]
    slices = [lil_matrix(slice.copy()) for slice in tensor]
    for slice in slices:
        for need in remove_needs:
            slice[need,:] = lil_matrix(np.zeros(slice.shape[0]))
            slice[:,need] = lil_matrix(np.zeros(slice.shape[0])).transpose()
    Tc = [csr_matrix(slice) for slice in slices]
    newHeaders = ["NULL" if i in remove_needs else headers[i] for i in range(len(headers))]
    return Tc, newHeaders

# predict connections by combining the execution of algorithms. First execute the cosine similarity
# algorithm (preferably choosing a threshold to get a high precision) and with this predicted matches execute the
# rescal algorithm afterwards (to increase the recall)
def predict_combine_cosine_rescal(input_tensor, headers, test_needs, idx_test, rank,
                                  rescal_threshold, cosine_threshold, useNeedTypeSlice=False):

    wants = want_indices(input_tensor, headers)
    offers = offer_indices(input_tensor, headers)

    # execute the cosine algorithm first
    binary_pred_cosine = cosinus_link_prediciton(input_tensor, need_indices(headers),
                                          offers, wants, test_needs, cosine_threshold, 0.0, False)

    # use the connection prediction of the cosine algorithm as input for rescal
    temp_tensor = [csr_matrix(binary_pred_cosine)] + input_tensor[CONNECTION_SLICE+1:]
    if not (useNeedTypeSlice):
        temp_tensor = [input_tensor[CONNECTION_SLICE]] + input_tensor[NEED_TYPE_SLICE+1:]
    A,R = execute_rescal(temp_tensor, rank)
    P_bin = predict_rescal_connections_by_threshold(A, R, rescal_threshold, offers, wants, test_needs)

    # return both predictions the earlier cosine and the combined rescal
    binary_pred_cosine = binary_pred_cosine[idx_test]
    binary_pred_rescal = matrix_to_array(P_bin, idx_test)
    return binary_pred_cosine, binary_pred_rescal

# predict connections by combining the execution of algorithms. Compute the predictions of connections for both
# cosine similarity and rescal algorithm. Then return the intersection of the predictions
def predict_intersect_cosine_rescal(input_tensor, headers, test_needs, idx_test, rank,
                                    rescal_threshold, cosine_threshold, useNeedTypeSlice=False):

    wants = want_indices(input_tensor, headers)
    offers = offer_indices(input_tensor, headers)

    # execute the cosine algorithm
    binary_pred_cosine = cosinus_link_prediciton(input_tensor, need_indices(headers),
                                                 offers, wants, test_needs, cosine_threshold, 0.0, False)

    # execute the rescal algorithm
    temp_tensor = input_tensor
    if not (useNeedTypeSlice):
        temp_tensor = [input_tensor[CONNECTION_SLICE]] + input_tensor[NEED_TYPE_SLICE+1:]
    A,R = execute_rescal(temp_tensor, rank)
    P_bin = predict_rescal_connections_by_threshold(A, R, rescal_threshold, offers, wants, test_needs)

    # return the intersection of the prediction of both algorithms
    binary_pred_cosine = binary_pred_cosine[idx_test]
    binary_pred_rescal = matrix_to_array(P_bin, idx_test)
    binary_pred = [min(binary_pred_cosine[i], binary_pred_rescal[i]) for i in range(len(binary_pred_cosine))]
    return binary_pred, binary_pred_cosine, binary_pred_rescal

# write precision/recall (and threshold) curve to file
def write_precision_recall_curve_file(folder, outfilename, precision, recall, threshold):
    if not os.path.exists(folder):
        os.makedirs(folder)
    _log.info("write precision-recall-curve file:" + folder + "/" + outfilename)
    file = codecs.open(folder + "/" + outfilename,'w+',encoding='utf8')
    file.write("precision, recall, threshold")
    prevline = ""
    for i in range(1, len(threshold)):
        line = "\n%.3f, %.3f, %.3f" % (precision[i], recall[i], threshold[i])
        if line != prevline:
            file.write(line)
            prevline = line
    file.close()

# write ROC curve with TP and FP (and threshold) to file
def write_ROC_curve_file(folder, outfilename, TP, FP, threshold):
    if not os.path.exists(folder):
        os.makedirs(folder)
    _log.info("write ROC-curve file:" + folder + "/" + outfilename)
    file = codecs.open(folder + "/" + outfilename,'w+',encoding='utf8')
    file.write("TP, FP, threshold")
    prevline = ""
    for i in range(1, len(threshold)):
        line = "\n%.3f, %.3f, %.3f" % (TP[i], FP[i], threshold[i])
        if line != prevline:
            file.write(line)
            prevline = line
    file.close()

# classify based on 2 values as true positive (TP), true negative (TN), false positive (FP), false negative (FN)
def test_classification(y_true, y_pred):
    if y_true == y_pred:
        if y_true == 1.0:
            return "TP"
        else:
            return "TN"
    else:
        if y_true == 1.0:
            return "FN"
        else:
            return "FP"

# helper function
def create_file_from_sorted_list(dir, filename, list):
    if not os.path.exists(dir):
        os.makedirs(dir)
    file = codecs.open(dir + "/" + filename,'w+',encoding='utf8')
    list.sort()
    for entry in list:
        file.write(entry + "\n")
    file.close()

# calculate precision
def calc_precision(TP, FP):
    return TP / float(TP + FP) if (TP + FP) > 0 else 1.0

# calculate recall
def calc_recall(TP, FN):
    return TP / float(TP + FN) if (TP + FN) > 0 else 1.0

# calculate accuracy
def calc_accuracy(TP, TN, FP, FN):
    return (TP + TN) / float(TP + TN + FP + FN) if (TP + TN + FP + FN) > 0 else 1.0

# in a specified folder create files which represent tested needs. For each of these files print the
# binary classifiers: TP, FP, FN including the (connected/not connected) need names for manual detailed analysis of
# the classification algorithm.
def output_statistic_details(outputpath, headers, con_slice_true, con_slice_pred, idx_test):
    TP, TN, FP, FN = 0,0,0,0
    need_list = []
    sorted_idx = np.argsort(idx_test[0])
    i1 = [idx_test[0][i] for i in sorted_idx]
    i2 = [idx_test[1][i] for i in sorted_idx]
    idx_test = (i1, i2)
    need_from = idx_test[0][0]
    need_to = idx_test[1][0]
    if not os.path.exists(outputpath):
        os.makedirs(outputpath)
    summary_file = codecs.open(outputpath + "/_summary.txt",'a+',encoding='utf8')
    class_label = test_classification(con_slice_true[need_from, need_to], con_slice_pred[need_from, need_to])
    need_list.append(class_label + ": " + headers[need_to])
    for i in range(1,len(idx_test[0])):
        need_from_prev = idx_test[0][i-1]
        need_from = idx_test[0][i]
        need_to = idx_test[1][i]
        if need_from_prev != need_from:
            create_file_from_sorted_list(outputpath, headers[need_from_prev][6:] + ".txt", need_list)
            summary_file.write(headers[need_from_prev][6:])
            summary_file.write(": TP: " + str(TP))
            summary_file.write(": TN: " + str(TN))
            summary_file.write(": FP: " + str(FP))
            summary_file.write(": FN: " + str(FN))
            summary_file.write(": Precision: " + str(calc_precision(TP, FP)))
            summary_file.write(": Recall: " + str(calc_recall(TP, FN)))
            summary_file.write(": Accuracy: " + str(calc_accuracy(TP, TN, FP, FN)) + "\n")
            need_list = []
            TP, TN, FP, FN = 0,0,0,0
        class_label = test_classification(con_slice_true[need_from, need_to], con_slice_pred[need_from, need_to])
        TP += (1 if class_label == "TP" else 0)
        TN += (1 if class_label == "TN" else 0)
        FP += (1 if class_label == "FP" else 0)
        FN += (1 if class_label == "FN" else 0)
        if class_label != "TN":
            need_list.append(class_label + ": " + headers[need_to])
    create_file_from_sorted_list(outputpath, headers[need_from_prev][6:] + ".txt", need_list)
    summary_file.write(headers[need_from_prev][6:])
    summary_file.write(": TP: " + str(TP))
    summary_file.write(": TN: " + str(TN))
    summary_file.write(": FP: " + str(FP))
    summary_file.write(": FN: " + str(FN))
    summary_file.write(": Precision: " + str(calc_precision(TP, FP)))
    summary_file.write(": Recall: " + str(calc_recall(TP, FN)))
    summary_file.write(": Accuracy: " + str(calc_accuracy(TP, TN, FP, FN)) + "\n")
    summary_file.close()

# calculate the optimal threshold by maximizing the f-score measure
def get_optimal_threshold(recall, precision, threshold, f_beta=1.0):
    max_f_score = 0
    optimal_threshold = 0.0
    for i in range(len(threshold)):
        r = recall[i]
        p = precision[i]
        div = (f_beta * f_beta * p + r)
        if div != 0:
            f_score = (1 + f_beta * f_beta) * (p * r) / div
            if f_score > max_f_score:
                max_f_score = f_score
                optimal_threshold = threshold[i]
    return optimal_threshold

# class to collect data during the runs of the test and print calculated measures for summary
class EvaluationReport:

    def __init__(self, f_beta=1.0):
        self.f_beta = f_beta
        self.precision = []
        self.recall = []
        self.accuracy = []
        self.fscore = []

    def add_evaluation_data(self, y_true, y_pred):
        p, r, f, _ =  m.precision_recall_fscore_support(y_true, y_pred, average='weighted', beta=self.f_beta)
        a = m.accuracy_score(y_true, y_pred)
        cm = m.confusion_matrix(y_true, y_pred, [1, 0])
        self.precision.append(p)
        self.recall.append(r)
        self.fscore.append(f)
        self.accuracy.append(a)
        _log.info('accuracy: %f' % a)
        _log.info('precision: %f' % p)
        _log.info('recall: %f' % r)
        _log.info('f%.01f-score: %f' % (self.f_beta, f))
        _log.info('confusion matrix: ' + str(cm))

    def summary(self):
        a = np.array(self.accuracy)
        p = np.array(self.precision)
        r = np.array(self.recall)
        f = np.array(self.fscore)
        _log.info('Accuracy Mean / Std: %f / %f' % (a.mean(), a.std()))
        _log.info('Precision Mean / Std: %f / %f' % (p.mean(), p.std()))
        _log.info('Recall Mean / Std: %f / %f' % (r.mean(), r.std()))
        _log.info('F%.01f-Score Mean / Std: %f / %f' % (self.f_beta, f.mean(), f.std()))


# This program executes a N-fold cross validation on rescal tensor data.
# For each fold test needs are randomly chosen and all their connections to
# all other needs are masked by 0 in the tensor. Then link prediction algorithms
# (e.g. RESCAL) are executed and measures are taken that describe the recovery of
# these masked connection entries.
# Different approaches for connection prediction between needs are tested:
# 1) RESCAL: choose a fixed threshold and take every connection that exceeds this threshold
# 2) RESCALSIM: choose a fixed threshold and compare need similarity to predict connections
# 3) COSINE: compute the cosine similarity between attributes of the needs
# 4) COSINE_WEIGHTED: compute the weighted cosine similarity between attributes of the needs
if __name__ == '__main__':

    # CLI processing
    parser = argparse.ArgumentParser(description='link prediction algorithm evaluation script')

    # general
    parser.add_argument('-inputfolder',
                        action="store", dest="inputfolder", required=True,
                        help="input folder of the evaluation")
    parser.add_argument('-outputfolder',
                        action="store", dest="outputfolder", required=False,
                        help="output folder of the evaluation")
    parser.add_argument('-header',
                        action="store", dest="headers", default="headers.txt",
                        help="name of header file")
    parser.add_argument('-connection_slice',
                        action="store", dest="connection_slice", default="connection.mtx",
                        help="name of connection slice file of the tensor")
    parser.add_argument('-needtype_slice',
                        action="store", dest="needtype_slice", default="needtype.mtx",
                        help="name of needtype slice file of the tensor")
    parser.add_argument('-additional_slices', action="store", required=True,
                        dest="additional_slices", nargs="+",
                        help="name of additional slice files to add to the tensor")

    # evaluation parameters
    parser.add_argument('-folds', action="store", dest="folds", default=10,
                        type=int, help="number of folds in cross fold validation")
    parser.add_argument('-maskrandom', action="store_true", dest="maskrandom",
                        help="mask random test connections (not per need)")
    parser.add_argument('-fbeta', action="store", dest="fbeta", default=0.5,
                        type=float, help="f-beta measure to calculate during evaluation")
    parser.add_argument('-maxconnections', action="store", dest="maxconnections", default=1000,
                        type=int, help="maximum number of connections used to lern from per need")
    parser.add_argument('-numneeds', action="store", dest="numneeds", default=10000,
                        type=int, help="number of needs used for the evaluation")
    parser.add_argument('-statistics', action="store_true", dest="statistics",
                        help="write detailed statistics for the evaluation")

    # algorithm parameters
    parser.add_argument('-rescal', action="store", dest="rescal", nargs=3,
                        metavar=('rank', 'threshold', 'useNeedTypeSlice'),
                        help="evaluate RESCAL algorithm")
    parser.add_argument('-rescalsim', action="store", dest="rescalsim", nargs=4,
                        metavar=('rank', 'threshold', 'useNeedTypeSlice', 'useConnectionSlice'),
                        help="evaluate RESCAL similarity algorithm")
    parser.add_argument('-cosine', action="store", dest="cosine", nargs=2,
                        metavar=('threshold', 'transitive_threshold'),
                        help="evaluate cosine similarity algorithm" )
    parser.add_argument('-cosine_weighted', action="store", dest="cosine_weigthed",
                        nargs=2, metavar=('threshold', 'transitive_threshold'),
                        help="evaluate weighted cosine similarity algorithm")
    parser.add_argument('-cosine_rescal', action="store", dest="cosine_rescal",
                        nargs=4, metavar=('rescal_rank', 'rescal_threshold', 'cosine_threshold', 'useNeedTypeSlice'),
                        help="evaluate combined algorithms cosine similarity and rescal")
    parser.add_argument('-intersection', action="store", dest="intersection",
                        nargs=4, metavar=('rescal_rank', 'rescal_threshold', 'cosine_threshold', 'useNeedTypeSlice'),
                        help="compute the prediction intersection of algorithms cosine similarity and rescal")

    args = parser.parse_args()
    folder = args.inputfolder

    start_time = strftime("%Y-%m-%d_%H%M%S")
    if args.outputfolder:
        outfolder = args.outputfolder
    else:
        outfolder = folder + "/out/" + start_time
    if not os.path.exists(outfolder):
        os.makedirs(outfolder)
    hdlr = logging.FileHandler(outfolder + "/eval_result_" + start_time + ".log")
    _log.addHandler(hdlr)

    # load the tensor input data
    data_input = [folder + "/" + args.connection_slice,
                  folder + "/" + args.needtype_slice]
    for slice in args.additional_slices:
        data_input.append(folder + "/" + slice)
    header_input = folder + "/" + args.headers
    input_tensor, headers = read_input_tensor(header_input, data_input, True)

    # TEST-PARAMETERS:
    # ===================

    # (10-)fold cross validation
    FOLDS = args.folds

    # True means: for testing mask all connections of random test needs (Test Case: Predict connections for new need
    # without connections)
    # False means: for testing mask random connections (Test Case: Predict connections for existing need which may
    # already have connections)
    MASK_ALL_CONNECTIONS_OF_TEST_NEED = not args.maskrandom

    # the f-beta-measure is used to calculate the optimal threshold for the rescal algorithm. beta=1 is the
    # F1-measure which weights precision and recall both same important. the higher the beta value,
    # the more important is recall compared to precision
    F_BETA = args.fbeta

    # by changing this parameter the number of training connections per need can be set. Choose a high value (e.g.
    # 100) to use all connection in the connections file. Choose a low number to restrict the number of training
    # connections (e.g. to 1 or even 0). This way tests are possible that describe situation where initially not many
    # connection are available to learn from.
    MAX_CONNECTIONS_PER_NEED = args.maxconnections

    # changing the rank parameter influences the amount of internal latent "clusters" of the algorithm and thus the
    # quality of the matching as well as performance (memory and execution time)
    RESCAL_RANK = (int(args.rescal[0]) if args.rescal else None)
    RESCAL_SIMILARITY_RANK = (int(args.rescalsim[0]) if args.rescalsim else None)

    # threshold for RESCAL algorithm connection slice, higher threshold means higher precision
    RESCAL_THRESHOLD = (float(args.rescal[1]) if args.rescal else None)

    # threshold for RESCAL algorithm need similarity, higher threshold means higher recall
    RESCAL_SIMILARITY_THRESHOLD = (float(args.rescalsim[1]) if args.rescalsim else None)

    # thresholds for cosine similarity link prediction algorithm, higher threshold means higher recall.
    # set transitive threshold < threshold to avoid transitive predictions
    COSINE_SIMILARITY_THRESHOLD = (float(args.cosine[0]) if args.cosine else None)
    COSINE_SIMILARITY_TRANSITIVE_THRESHOLD = (float(args.cosine[1]) if args.cosine else None)
    COSINE_WEIGHTED_SIMILARITY_THRESHOLD = (float(args.cosine_weigthed[0]) if args.cosine_weigthed else None)
    COSINE_WEIGHTED_SIMILARITY_TRANSITIVE_THRESHOLD = (float(args.cosine_weigthed[1]) if args.cosine_weigthed else None)

    _log.info('------------------------------')
    _log.info('Test Setup:')
    _log.info('------------------------------')


    if (args.numneeds < len(need_indices(headers))):
        input_tensor, headers = keep_x_random_needs(input_tensor, headers, args.numneeds)

    GROUND_TRUTH = [input_tensor[i].copy() for i in range(len(input_tensor))]
    needs = need_indices(headers)
    np.random.shuffle(needs)
    connections = need_connection_indices(need_indices(headers), needs)

    if MASK_ALL_CONNECTIONS_OF_TEST_NEED:
        _log.info('For testing mask all connections of random test needs (Test Case: Predict connections for new need '
                  'without connections)')
    else:
        _log.info('For testing mask random connections (Test Case: Predict connections for existing need which may '
                  'already have connections)')

    _log.info('For testing use a maximum number of %d connections per need' % MAX_CONNECTIONS_PER_NEED)
    input_tensor = mask_all_but_X_connections_per_need(input_tensor, MAX_CONNECTIONS_PER_NEED)
    offers = offer_indices(input_tensor, headers)
    wants = want_indices(input_tensor, headers)
    need_fold_size = int(len(needs) / FOLDS)
    connection_fold_size = int(len(connections[0]) / FOLDS)
    AUC_test = np.zeros(FOLDS)
    report1 = EvaluationReport(F_BETA)
    report2 = EvaluationReport(F_BETA)
    report3 = EvaluationReport(F_BETA)
    report4 = EvaluationReport(F_BETA)
    report5 = EvaluationReport(F_BETA)
    report6 = EvaluationReport(F_BETA)
    report7 = EvaluationReport(F_BETA)
    report8 = EvaluationReport(F_BETA)
    report9 = EvaluationReport(F_BETA)

    _log.info('Number of test needs: %d (OFFERS: %d, WANTS: %d)' %
              (len(needs), len(set(needs) & set(offers)), len(set(needs) & set(wants))))
    _log.info('Number of total needs: %d (OFFERS: %d, WANTS: %d)' %
              (len(need_indices(headers)), len(offers), len(wants)))
    _log.info('Number of test and train connections: %d' % len(connection_indices(input_tensor)[0]))
    _log.info('Number of total connections (for evaluation): %d' % len(connection_indices(GROUND_TRUTH)[0]))
    _log.info('Number of attributes: %d' % len(attribute_indices(headers)))

    _log.info('Starting %d-fold cross validation' % FOLDS)

    # start the cross validation
    offset = 0
    for f in range(FOLDS):

        _log.info('------------------------------')
        # define test set of connections indices
        if MASK_ALL_CONNECTIONS_OF_TEST_NEED:
            # choose the test needs for the fold and mask all connections of them to other needs
            _log.info('Fold %d, fold size %d needs (out of %d)' % (f, need_fold_size, len(needs)))
            test_needs = needs[offset:offset+need_fold_size]
            test_tensor = mask_need_connections(input_tensor, test_needs)
            idx_test = need_connection_indices(need_indices(headers), test_needs)
            offset += need_fold_size
        else:
            # choose test connections to mask independently of needs
            _log.info('Fold %d, fold size %d connection indices (out of %d)' % (f, connection_fold_size,
                                                                                len(connections[0])))
            idx_test = (connections[0][offset:offset+connection_fold_size],
                        connections[1][offset:offset+connection_fold_size])
            test_tensor = mask_idx_connections(input_tensor, idx_test)

            offset += connection_fold_size
            test_needs = needs
        _log.info('------------------------------')

        # evaluate the algorithms
        if args.rescal:
            # execute the rescal algorithm
            temp_tensor = test_tensor
            if not (args.rescal[2] == 'True'):
                temp_tensor = [test_tensor[CONNECTION_SLICE]] + test_tensor[NEED_TYPE_SLICE+1:]
                _log.info('Do not use needtype slice for RESCAL')
            A, R = execute_rescal(temp_tensor, RESCAL_RANK)

            # evaluate the predictions
            _log.info('start predict connections ...')
            prediction = np.round_(predict_rescal_connections_array(A, R, idx_test), decimals=5)
            _log.info('stop predict connections')
            precision, recall, threshold = m.precision_recall_curve(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE],idx_test), prediction)
            optimal_threshold = get_optimal_threshold(recall, precision, threshold, F_BETA)
            _log.info('optimal RESCAL threshold would be ' + str(optimal_threshold) +
                      ' (for maximum F' + str(F_BETA) + '-score)')

            AUC_test[f] = m.auc(recall, precision)
            _log.info('AUC test: ' + str(AUC_test[f]))

            # use a fixed threshold to compute several measures
            _log.info('For RESCAL prediction with threshold %f:' % RESCAL_THRESHOLD)
            P_bin = predict_rescal_connections_by_threshold(A, R, RESCAL_THRESHOLD, offers, wants, test_needs)
            binary_pred = matrix_to_array(P_bin, idx_test)
            report1.add_evaluation_data(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE],idx_test), binary_pred)
            if args.statistics:
                write_precision_recall_curve_file(outfolder + "/statistics/rescal_" + start_time,
                                                  "precision_recall_curve_fold%d.csv" % f, precision, recall, threshold)
                TP, FP, threshold = m.roc_curve(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE],idx_test), prediction)
                write_ROC_curve_file(outfolder + "/statistics/rescal_" + start_time, "ROC_curve_fold%d.csv" % f, TP, FP, threshold)
                if MASK_ALL_CONNECTIONS_OF_TEST_NEED:
                    output_statistic_details(outfolder + "/statistics/rescal_" + start_time, headers, GROUND_TRUTH[CONNECTION_SLICE], P_bin, idx_test)

        if args.rescalsim:
            # execute the rescal algorithm
            temp_tensor = test_tensor[NEED_TYPE_SLICE+1:]
            if (args.rescalsim[2] == 'True'):
                temp_tensor = [test_tensor[NEED_TYPE_SLICE]] + temp_tensor
            if (args.rescalsim[3] == 'True'):
                temp_tensor = [test_tensor[CONNECTION_SLICE]] + temp_tensor
                _log.info('Do not use needtype slice for RESCAL')
            A, R = execute_rescal(temp_tensor, RESCAL_SIMILARITY_RANK)

            # use the most similar needs per need to predict connections
            _log.info('For RESCAL prediction based on need similarity with threshold: %f' % RESCAL_SIMILARITY_THRESHOLD)
            P_bin = predict_rescal_connections_by_need_similarity(A, RESCAL_SIMILARITY_THRESHOLD, offers, wants, test_needs)
            binary_pred = matrix_to_array(P_bin, idx_test)
            report2.add_evaluation_data(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE], idx_test), binary_pred)

            if args.statistics:
                S = similarity_ranking(A)
                y_prop = [1.0 - i for i in np.nan_to_num(S[idx_test])]
                precision, recall, threshold = m.precision_recall_curve(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE], idx_test), y_prop)
                write_precision_recall_curve_file(outfolder + "/statistics/rescal_similarity_" + start_time, "precision_recall_curve_fold%d.csv" % f, precision, recall, threshold)
                TP, FP, threshold = m.roc_curve(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE], idx_test), y_prop)
                write_ROC_curve_file(outfolder + "/statistics/rescal_similarity_" + start_time, "ROC_curve_fold%d.csv" % f, TP, FP, threshold)
                if MASK_ALL_CONNECTIONS_OF_TEST_NEED:
                    output_statistic_details(outfolder + "/statistics/rescal_similarity_" + start_time, headers, GROUND_TRUTH[CONNECTION_SLICE], P_bin, idx_test)

        if args.cosine:
            # execute the cosine similarity link prediction algorithm
            _log.info('For prediction of cosine similarity between needs with thresholds: %f, %f'
                      ':' % (COSINE_SIMILARITY_THRESHOLD, COSINE_SIMILARITY_TRANSITIVE_THRESHOLD))
            binary_pred = cosinus_link_prediciton(test_tensor, need_indices(headers),
                                                  offers, wants, test_needs, COSINE_SIMILARITY_THRESHOLD,
                                                  COSINE_SIMILARITY_TRANSITIVE_THRESHOLD, False)
            report3.add_evaluation_data(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE],idx_test), binary_pred[idx_test])
            if MASK_ALL_CONNECTIONS_OF_TEST_NEED and args.statistics:
                output_statistic_details(outfolder + "/statistics/cosine_" + start_time, headers, GROUND_TRUTH[CONNECTION_SLICE],
                                         binary_pred, idx_test)

        if args.cosine_weigthed:
            # execute the weighted cosine similarity link prediction algorithm
            _log.info('For prediction of weigthed cosine similarity between needs with thresholds %f, %f:' %
                      (COSINE_WEIGHTED_SIMILARITY_THRESHOLD, COSINE_WEIGHTED_SIMILARITY_TRANSITIVE_THRESHOLD))
            binary_pred = cosinus_link_prediciton(test_tensor, need_indices(headers), offers, wants, test_needs,
                                                  COSINE_WEIGHTED_SIMILARITY_THRESHOLD,
                                                  COSINE_WEIGHTED_SIMILARITY_TRANSITIVE_THRESHOLD, True)
            report4.add_evaluation_data(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE],idx_test), binary_pred[idx_test])
            if MASK_ALL_CONNECTIONS_OF_TEST_NEED and args.statistics:
                output_statistic_details(outfolder + "/statistics/weighted_cosine_" + start_time, headers,
                                     GROUND_TRUTH[CONNECTION_SLICE], binary_pred, idx_test)

        if args.cosine_rescal:
            cosine_pred, rescal_pred = predict_combine_cosine_rescal(test_tensor, headers, test_needs, idx_test,
                                                                     int(args.cosine_rescal[0]),
                                                                     float(args.cosine_rescal[1]),
                                                                     float(args.cosine_rescal[2]),
                                                                     bool(args.cosine_rescal[3]))
            _log.info('First step for prediction of cosine similarity with threshold: %f:' % float(args.cosine_rescal[2]))
            report5.add_evaluation_data(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE],idx_test), cosine_pred)
            _log.info('And second step for combined RESCAL prediction with parameters: %d, %f:'
                      % (int(args.cosine_rescal[0]), float(args.cosine_rescal[1])))
            report6.add_evaluation_data(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE],idx_test), rescal_pred)

        if args.intersection:
            inter_pred, cosine_pred, rescal_pred = predict_intersect_cosine_rescal(test_tensor, headers, test_needs,
                                                                                   idx_test, int(args.intersection[0]), float(args.intersection[1]),
                                                                                   float(args.intersection[2]), bool(args.intersection[3]))
            _log.info('Intersection of predictions of cosine similarity and rescal algorithms: ')
            report9.add_evaluation_data(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE],idx_test), inter_pred)

            _log.info('For RESCAL prediction with threshold %f:' % float(args.intersection[1]))
            report8.add_evaluation_data(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE],idx_test), rescal_pred)

            _log.info('For prediction of cosine similarity between needs with thresholds: %f:' %
                      float(args.intersection[2]))
            report7.add_evaluation_data(matrix_to_array(GROUND_TRUTH[CONNECTION_SLICE],idx_test), cosine_pred)

        # end of fold loop

    _log.info('====================================================')
    if args.rescal:
        _log.info('AUC-PR Test Mean / Std: %f / %f' % (AUC_test.mean(), AUC_test.std()))
        _log.info('----------------------------------------------------')
        _log.info('For RESCAL prediction with threshold %f:' % RESCAL_THRESHOLD)
        report1.summary()
        _log.info('----------------------------------------------------')
    if args.rescalsim:
        _log.info('For RESCAL prediction based on need similarity with threshold: %f' % RESCAL_SIMILARITY_THRESHOLD)
        report2.summary()
        _log.info('----------------------------------------------------')
    if args.cosine:
        _log.info('For prediction of cosine similarity between needs with thresholds: %f, %f'
                  ':' % (COSINE_SIMILARITY_THRESHOLD, COSINE_SIMILARITY_TRANSITIVE_THRESHOLD))
        report3.summary()
        _log.info('----------------------------------------------------')
    if args.cosine_weigthed:
        _log.info('For prediction of weighted cosine similarity between needs with thresholds: %f, %f'
                  ':' % (COSINE_SIMILARITY_THRESHOLD, COSINE_SIMILARITY_TRANSITIVE_THRESHOLD))
        report4.summary()
    if args.cosine_rescal:
        _log.info('First step for prediction of cosine similarity with threshold: %f:' % float(args.cosine_rescal[2]))
        report5.summary()
        _log.info('And second step for combined RESCAL prediction with threshold: %f:' % float(args.cosine_rescal[1]))
        report6.summary()
    if args.intersection:
        _log.info('Intersection of predictions of cosine similarity and rescal algorithms: ')
        report9.summary()
        _log.info('For RESCAL prediction with threshold %f:' % float(args.intersection[1]))
        report8.summary()
        _log.info('For prediction of cosine similarity between needs with thresholds: %f:' %
                  float(args.intersection[2]))
        report7.summary()





