import torch
import gc
import warnings
import clip
import re
from random import sample

def clear_mem():
    torch.cuda.empty_cache()
    gc.collect()

#perceptor, clip_preprocess = clip.load('ViT-B/16')
device = "cuda" if torch.cuda.is_available() else "cpu"
perceptor, clip_preprocess = clip.load('ViT-B/32', device=device)
perceptor.eval().float().requires_grad_(False)

tokenizer = clip.simple_tokenizer.SimpleTokenizer()

class synonymiser:
    def __init__(self, prompt:str, topk:int):
        self.prompt = prompt
        self.topk = topk
        self.stripcommas = False
        
    def asIs(self):
        return self.prompt
    
    def convert_string_to_array(string):
        # Remove punctuation and numbers
        string = re.sub(r'[^a-zA-Z ]', '', string)
        
        # Split into a list of words
        words_list = string.split()

        # Remove words with length less than or equal to 2
        words_list = [word for word in words_list if len(word) > 2]

        return words_list

    def synonymise(self):
        target_tokens = tokenizer.encode(self.prompt)
        new_prompt = ""
        num_prompt = 0
        total_index = 0
        for now_token in target_tokens:
            target_emb = perceptor.token_embedding.weight[now_token,None].detach()
            token_sim  = torch.cosine_similarity(target_emb,perceptor.token_embedding.weight.detach(),-1)
            top_token_sim = torch.topk(token_sim,self.topk+1,-1,True,True)
            top_indices = top_token_sim.indices[1:]
            top_values  = top_token_sim.values[1:]
            output = []
            for i in range(top_indices.shape[0]):
                output.append([tokenizer.decode([top_indices[i].item()]), top_values[i].item()]) 
            shuffle_output = sample(output, len(output))
            new_token = shuffle_output[0][0]
            new_index = shuffle_output[0][1]
            new_prompt += new_token
            total_index += new_index
            num_prompt += 1
        total_index = total_index / num_prompt
        # strip any unicode characters from new_prompt
        new_prompt = new_prompt.encode('ascii', 'ignore').decode('ascii')
        # strip any commas from new_prompt
        if self.stripcommas:
            new_prompt = new_prompt.replace(", ","")
        return new_prompt, total_index


class comfyui_promptsynonymiser:
    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "prompt": ("STRING", {"default": '', "multiline": True}),
                "num_synonyms": ("INT", {"default": 0, "min": 0, "max": 10}),
                "top_k": ("INT", {"default": 1, "min": 1, "max": 32})
            }
        }

    RETURN_TYPES = ("STRING", "STRING", )
    FUNCTION = "comfyui_promptsynonymiser_run"

    CATEGORY = "Sathias"

    def comfyui_promptsynonymiser_run(self, prompt, num_synonyms, top_k):
        words = synonymiser.convert_string_to_array(prompt)
        # if num_synonyms is 0, synonymise each word in the prompt
        if num_synonyms == 0:
            num_synonyms = len(words)
        # if num_synonyms is greater than the number of words in the prompt, set it to the number of words in the prompt
        if num_synonyms > len(words):
            num_synonyms = len(words)
        # synonymise the prompt
        new_prompt = prompt
        for i in range(num_synonyms):
            # select a word at random
            word = sample(words, 1)[0]
            # synonymise the word
            s = synonymiser(word, top_k)
            new_word, index = s.synonymise()
            # trim the word and strip out any unicode characters
            new_word = new_word.strip().encode('ascii', 'ignore').decode('ascii')
            # replace the word in the prompt with the synonym
            new_prompt = new_prompt.replace(word, new_word)
            # remove the word from the list of words
            words.remove(word)
        return new_prompt, prompt
        
NODE_CLASS_MAPPINGS = {
    "PromptSynonymiser": comfyui_promptsynonymiser
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "PromptSynonymiser": "PromptSynonymiser Node"
}

    
