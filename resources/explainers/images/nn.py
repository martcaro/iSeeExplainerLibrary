from http.client import BAD_REQUEST
from flask_restful import Resource
from flask import request
from PIL import Image
import os
import torch.nn as nn
import numpy as np
import tensorflow as tf
import torch
import h5py
import heapq
import json
from io import BytesIO
import matplotlib.pyplot as plt
from getmodelfiles import get_model_files
from utils import ontologyConstants
from utils.base64 import base64_to_vector, PIL_to_base64
from utils.img_processing import normalize_img, normalise_image_batch, denormalise_image_batch
from utils.validation import validate_params
from sklearn.metrics.pairwise import euclidean_distances
import traceback

class NearestNeighboursImage(Resource):

    def __init__(self,model_folder,upload_folder):
        self.model_folder = model_folder
        self.upload_folder = upload_folder
        
    def nn_data(self, label_raw, label, model_info, encoder, data_file,sample=None):
        train_data = []
        
        if type(data_file)==str and os.path.isdir(data_file):
            # classification image dataset in zipped folder
            _folders = [_f for _f in os.listdir(data_file) if _f == label_raw]
            if len(_folders)!=1:
                raise Exception("No data found.")

            _folder_path = os.path.join(data_file, _folders[0])
            _files = [os.path.join(_folder_path, f) for f in os.listdir(_folder_path)]
            train_data = np.asarray([np.array(Image.open(f)) for f in _files])

            if sample!=None:
                train_data=train_data[np.random.randint(train_data.shape[0], size=min(sample,len(train_data))), :]
            print(train_data.shape)
            train_data = normalise_image_batch(train_data, model_info)
            train_encodings = encoder(train_data)

            return train_data, train_encodings
        
        # if os.path.isfile(data_file):
        #     # csv file, first column is column names, 1st column maybe index 
        #     with open(data_file, 'r') as f:
        #         header = next(f).split(' ')
        #         header = [elem.strip() for elem in header]

        #         while True:
        #             try:
        #                 s_instance = next(f)
        #                 s_instance = s_instance.replace('\n', '')
        #                 s_array = s_instance.split(',')
        #                 if label == float(s_array[-1]):
        #                     s_array = [float(s) for s in s_array][:-2]
        #                     train_data.append(s_array)
        #             except Exception as e: #end of rows
        #                 train_data = np.asarray(train_data, dtype=float)
        #                 train_data = train_data.reshape((train_data.shape[0],)+tuple(model_info["attributes"]["features"]["image"]["shape"]))
        #                 train_encodings = encoder(train_data)
        #                 return train_data, train_encodings        
        
        else:
            header = next(data_file).split(',')
            header = [elem.strip() for elem in header]

            while True:
                try:
                    s_instance = next(data_file)
                    s_instance = s_instance.replace('\n', '')
                    s_array = s_instance.split(',')
                    if label == float(s_array[-1]):
                        s_array = [float(s) for s in s_array][:-1]
                        train_data.append(s_array)
                except Exception as e: #end of rows
                    
                    train_data=np.array(train_data)
                    if sample!=None:
                        train_data=train_data[np.random.randint(train_data.shape[0], size=min(sample,len(train_data))), :]
                    train_data = np.asarray(train_data, dtype=float)
                    train_data = train_data.reshape((train_data.shape[0],)+tuple(model_info["attributes"]["features"]["image"]["shape"]))
                    train_encodings = encoder(train_data)
                    return train_data, train_encodings                 
                    
    def knn(self, sample_size, data, query):
        ecd = euclidean_distances(query, data)[0]
        top = heapq.nsmallest(sample_size+1, range(len(ecd)), ecd.take)
        return top
 
    def post(self):
        params = request.json
        if params is None:
            return "The params are missing",BAD_REQUEST

        #check params
        if("id" not in params):
            return "The model id was not specified in the params.",BAD_REQUEST
        if("type" not in params):
            return "The instance type was not specified in the params.",BAD_REQUEST
        if("instance" not in params):
            return "The instance was not specified in the params.",BAD_REQUEST
        
        _id =params["id"]
        instance = params["instance"]
        params_json={}
        if "params" in params:
            params_json=params["params"]
        params_json=validate_params(params_json,self.get(_id)["params"])

        return self.explain(_id, instance, params_json)
    
    def explain(self, model_id, instance, params_json):
        try:
            

            #Getting model info, data, and file from local repository
            model_file, model_info_file, data_file = get_model_files(model_id,self.model_folder)

            ## params from info
            model_info=json.load(model_info_file)
            backend = model_info["backend"]  ##error handling?
            output_names=model_info["attributes"]["features"][model_info["attributes"]["target_names"][0]]["values_raw"]

            predic_func=None
            last_layer_func=None

            if model_file!=None:
                if backend in ontologyConstants.TENSORFLOW_URIS:
                    model = h5py.File(model_file, 'w')
                    model = tf.keras.models.load_model(model)
                    predic_func=model   
                    def last_layer(x):
                        new_model = tf.keras.models.Model([model.inputs], [model.layers[-2].output])
                        return new_model(x)
                    last_layer_func = last_layer
                elif backend in ontologyConstants.PYTORCH_URIS:
                    model = torch.load(model_file)
                    predic_func=model.predict
                    def last_layer(x):
                        new_model = nn.Sequential(*list(model.children())[:-1])
                        return new_model(x).flatten()
                    last_layer_func = last_layer
                else:
                    return "Only Tensorflow and PyTorch backends are supported.",BAD_REQUEST
            else:
                return "A ML model must be provided.",BAD_REQUEST
        
            try:
                instance = base64_to_vector(instance)
            except Exception as e:  
                return "Could not convert base64 Image to vector: " + str(e),BAD_REQUEST

            instance_raw = instance #Raw format needed for explanation

            #normalise and reshape
            try:
                instance=normalize_img(instance,model_info)
            except Exception as e:
                    return  "Could not normalize instance: " + str(e),BAD_REQUEST

            pred=np.array(predic_func(instance)[0])
            if(len(pred.shape)==1):
                instance_label = int(np.argmax(pred))
            else:
                instance_label=pred
            instance_label_raw = output_names[instance_label]

            no_neighbours = params_json["no_neighbours"]
            sample=params_json["samples"]

            train_data, train_encodings = self.nn_data(instance_label_raw, instance_label, model_info, last_layer_func, data_file,sample=sample)
            nn_indices = self.knn(no_neighbours, train_encodings, last_layer_func(instance))
            nn_instances = np.array([train_data[n] for n in nn_indices[1:]])
            nn_instances = denormalise_image_batch(nn_instances, model_info)

            size=(params_json["png_width"]/100.0,params_json["png_height"]/100.0)

            fig, axes = plt.subplots(nrows=1, ncols=nn_instances.shape[0]+1, figsize=size)
            axes[0].imshow(Image.fromarray(instance_raw))
            axes[0].set_title("Original Image")
            nn_instances = np.squeeze(nn_instances, axis=3) if nn_instances.shape[-1] == 1 else nn_instances

            print(nn_instances.shape)
            for i in range(nn_instances.shape[0]):
                axes[i+1].imshow(Image.fromarray(nn_instances[i]))
                axes[i+1].set_title("Nearest Neighbour "+str(i+1))
        
            for ax in fig.axes:
                ax.axis('off')
    
            #saving
            img_buf = BytesIO()
            fig.savefig(img_buf,bbox_inches='tight')
            im = Image.open(img_buf)
            b64Image=PIL_to_base64(im)

            exp_json={"Original Image": instance_raw.tolist()}
            i=1
            for nn in nn_instances:
                exp_json["Neighbour "+str(i)]=nn.tolist()
                i=i+1

            response={"type":"image","explanation":b64Image,"explanation_llm":exp_json}
            return response
        except:
            return traceback.format_exc(), 500

    def get(self,id=None):
        return {
        "_method_description": "Finds the nearest neighbours to a data instances based on minimum euclidean distance",
        "id": "Identifier of the ML model that was stored locally.",
        "instance": "Image to be explained in BASE64 format",
        "params": { 
                "no_neighbours":{
                    "description": "number of neighbours returned as an integer; default is 3.",
                    "type":"int",
                    "default": 3,
                    "range":None,
                    "required":False
                    },
                "samples":{
                    "description": "Number of samples to use from the background data. A hundred samples are used by default.",
                    "type":"int",
                    "default": 100,
                    "range":None,
                    "required":False
                    },
                "png_width":{
                    "description": "Width (in pixels) of the png image containing the explanation.",
                    "type":"int",
                    "default": 1200,
                    "range":None,
                    "required":False
                    },
                "png_height": {
                    "description": "Height (in pixels) of the png image containing the explanation.",
                    "type":"int",
                    "default": 600,
                    "range":None,
                    "required":False
                    }
                },
        "output_description":{
                "0":"This explanation presents nearest neighbours to the query; nearest neighbours are examples that are similar to the query with similar AI system outcomes."
            },

        "meta":{
                "modelAccess":"File",
                "supportsBWImage":True,
                "needsTrainingData": True


        }
    }
