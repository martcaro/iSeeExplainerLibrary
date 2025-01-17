from http.client import BAD_REQUEST
from flask_restful import Resource
from flask import request
import tensorflow as tf
import torch
import numpy as np
import joblib
import h5py
import json
import lime.lime_text
import os
from html2image import Html2Image
from getmodelfiles import get_model_files
from utils import ontologyConstants
from utils.base64 import PIL_to_base64
from utils.validation import validate_params
from PIL import Image
import requests
import traceback

class LimeText(Resource):

    def __init__(self,model_folder,upload_folder):
        self.model_folder = model_folder
        self.upload_folder = upload_folder

    def post(self):
        try:
            params = request.json
            if params is None:
                return "The json body is missing.",BAD_REQUEST
        
            #Check params
            if("id" not in params):
                return "The model id was not specified in the params.",BAD_REQUEST
            if("type" not in params):
                return "The instance type was not specified in the params.",BAD_REQUEST
            if("instance" not in params):
                return "The instance was not specified in the params.",BAD_REQUEST

            _id =params["id"]
            if("type"  in params):
                inst_type=params["type"]
            instance=params["instance"]
            url=None
            if "url" in params:
                url=params["url"]
            params_json={}
            if "params" in params:
                params_json=params["params"]
            params_json=validate_params(params_json,self.get(_id)["params"])
        
            #getting model info, data, and file from local repository
            model_file, model_info_file, _ = get_model_files(_id,self.model_folder)

            ##getting params from info
            model_info=json.load(model_info_file)
            backend = model_info["backend"] 

            label=model_info["attributes"]["target_names"][0]
        
            try:
                output_names=model_info["attributes"]["features"][label]["values_raw"]
            except:
                output_names=None

            if model_file!=None:
                if backend in ontologyConstants.TENSORFLOW_URIS:
                    model=h5py.File(model_file, 'w')
                    mlp = tf.keras.models.load_model(model)
                    predic_func=mlp
                elif backend in ontologyConstants.SKLEARN_URIS:
                    mlp = joblib.load(model_file)
                    try:
                        predic_func=mlp.predict_proba
                    except:
                        predic_func=mlp.predict
                elif backend in ontologyConstants.PYTORCH_URIS:
                    mlp = torch.load(model_file)
                    predic_func=mlp.predict
                else:
                    try:
                        mlp = joblib.load(model_file)
                        predic_func=mlp.predict
                    except Exception as e:
                        return "Could not extract prediction function from model: " + str(e),BAD_REQUEST
            elif url!=None:
                def predict(X):
                    return np.array(json.loads(requests.post(url, data=dict(inputs=str(X.tolist()))).text))
                predic_func=predict
            else:
                return "Either a stored model or a valid URL for the prediction function must be provided.",BAD_REQUEST
        
     
            # Create explainer
            explainer = lime.lime_text.LimeTextExplainer(class_names=output_names)
            kwargsData2 = dict(labels=None, top_labels=1, num_features=None)

            if params_json["top_classes"] and output_names: #if classification
                kwargsData2["top_labels"] = params_json["top_classes"]   #top labels
            if params_json["output_classes"] and output_names: #labels (if classification)
                kwargsData2["labels"] = [output_names.index(c) for c in params_json["output_classes"]]
                kwargsData2["top_labels"]=None #override top_classes

            if "num_features" in params_json:
                kwargsData2["num_features"] = int(params_json["num_features"])

            explanation = explainer.explain_instance(instance, predic_func, **{k: v for k, v in kwargsData2.items() if v is not None}) 
        
            ##formatting json explanation
            #ret = explanation.as_map()
            #ret = {str(k):[(int(i),float(j)) for (i,j) in v] for k,v in ret.items()}
            #if output_names!=None:
            #    ret = {output_names[int(k)]:v for k,v in ret.items()}
            #ret=json.loads(json.dumps(ret))

            #saving
            hti = Html2Image()
            hti.output_path= os.getcwd()

            css="body {background: white;}"
            if params_json["png_height"] and params_json["png_height"] in params_json:
                size=(params_json["png_width"],params_json["png_height"])
                hti.screenshot(html_str=explanation.as_html(), css_str=css, save_as="temp.png", size=size)   
            else:
                hti.screenshot(html_str=explanation.as_html(),css_str=css, save_as="temp.png")

            im=Image.open("temp.png")
            b64Image=PIL_to_base64(im)
            os.remove("temp.png")

            response={"type":"image","explanation":b64Image,"explanation_llm":json.loads(json.dumps(dict(explanation.as_list(explanation.available_labels()[0]))))}
            return response
        except:
            return traceback.format_exc(), 500

    def get(self,id=None):
        base_dict= {
        "_method_description": "LIME perturbs the input data samples in order to train a simple model that approximates the prediction for the given instance and similar ones. "
                           "The explanation contains the weight of each word to the prediction value. This method accepts 4 arguments: " 
                           "the 'id', the 'instance', the 'url',  and the 'params' JSON with the configuration parameters of the method. "
                           "These arguments are described below.",
        "id": "Identifier of the ML model that was stored locally.",
        "instance": "A string with the text to be explained.",
        "url": "External URL of the prediction function. Ignored if a model file was uploaded to the server. "
               "This url must be able to handle a POST request receiving a (multi-dimensional) array of N data points as inputs (instances represented as arrays). It must return a array of N outputs (predictions for each instance).",
        "params": { 

                "output_classes" : {
                    "description":  "Array of strings representing the names of the classes to be explained. Overrides 'top_classes' if provided.",
                    "type":"array",
                    "default": None,
                    "range":None,
                    "required":False
                    },
                "top_classes":{
                        "description": "Integer representing the number of classes with the highest prediction probability to be explained. ",
                        "type":"int",
                        "default": 1,
                        "range":None,
                        "required":False
                    },
                "num_features": {
                        "description": "Integer representing the maximum number of features to be included in the explanation.",
                        "type":"int",
                        "default": 10,
                        "range":[100,4096],
                        "required":False
                    },
                "png_width":{
                    "description": "Width (in pixels) of the png image containing the explanation.",
                    "type":"int",
                    "default": None,
                    "range":None,
                    "required":False
                    },
                "png_height": {
                    "description": "Height (in pixels) of the png image containing the explanation.",
                    "type":"int",
                    "default": None,
                    "range":[100,4096],
                    "required":False
                    }
                },
        "output_description":{
                "lime_plot": "An image contaning a plot with the most important words for the given instance. For regression models, the plot displays both positive and negative contributions of each word to the predicted outcome."
                "The same applies to classification models, but there can be a plot for each possible class. The text instance with highlighted words is included in the explanation."
               },
        "meta":{
                "modelAccess":"Any",
                "supportsBWImage":False,
                "needsTrainingData": False
         }
        }

        if id is not None:
            #Getting model info, data, and file from local repository
            try:
                _, model_info_file, _ = get_model_files(id,self.model_folder)
            except:
                return base_dict

            model_info=json.load(model_info_file)
            target_name=model_info["attributes"]["target_names"][0]


            if model_info["attributes"]["features"][target_name]["data_type"]=="categorical":

                output_names=model_info["attributes"]["features"][target_name]["values_raw"]

                base_dict["params"]["output_classes"]["default"]=None
                base_dict["params"]["output_classes"]["range"]=output_names

                base_dict["params"]["top_classes"]["range"]=[1,len(output_names)]

                return base_dict

            else:
                base_dict["params"].pop("output_classes")
                base_dict["params"].pop("top_classes")
                return base_dict

        else:
            return base_dict
