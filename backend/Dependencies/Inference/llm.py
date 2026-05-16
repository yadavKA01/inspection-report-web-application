
import base64
import boto3
import certifi
import config
import json
import os
import requests
import warnings
import time
from botocore.exceptions import BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError
from botocore.config import Config
from langchain_aws import BedrockEmbeddings, ChatBedrock

warnings.filterwarnings('ignore')

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

SONNET_3_MODEL_ID = "anthropic.claude-3-sonnet-20240229-v1:0" 
SONNET_35_MODEL_ID = "apac.anthropic.claude-3-5-sonnet-20240620-v1:0"
SONNET_37_MODEL_ID = "apac.anthropic.claude-3-7-sonnet-20250219-v1:0"
SONNET_4_MODEL_ID = "apac.anthropic.claude-sonnet-4-20250514-v1:0"


class LLM():
    def __init__(self,connectionTimeout=55, max_pool_connections=100, readTimeout=55):
        configBoto = Config(connect_timeout=connectionTimeout, max_pool_connections=max_pool_connections, read_timeout=readTimeout)
        config.InitConfiguration()
        self._awsConfig = config.GetConfiguration("AWS") or {}
        self._geminiConfig = config.GetConfiguration("GEMINI") or {}
        self._geminiApiKey = (self._geminiConfig.get("GEMINI_25_PRO_API_KEY") or "").strip()
        self._groqConfig = config.GetConfiguration("GROQ") or {}
        self._groqApiKey = (self._groqConfig.get("API_KEY") or os.environ.get("GROQ_API_KEY") or "").strip()
        self._openaiConfig = config.GetConfiguration("OPENAI") or {}
        self._openaiApiKey = (
            (self._openaiConfig.get("openai_api_key") or self._openaiConfig.get("API_KEY") or "")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        ).strip()

        ak = (self._awsConfig.get("ACCESS_KEY_ID") or "").strip()
        sk = (self._awsConfig.get("SECRET_ACCESS_KEY") or "").strip()
        if ak and sk:
            boto3Session = boto3.session.Session(
                aws_access_key_id=ak,
                aws_secret_access_key=sk,
                region_name=self._awsConfig.get("REGION", "us-east-1"),
            )
            self._bedrockRuntime = boto3Session.client("bedrock-runtime", config=configBoto, verify=certifi.where())
            self._bedrockAgentRuntime = boto3Session.client("bedrock-agent-runtime", config=configBoto, verify=certifi.where())
            self._s3Client = boto3Session.client("s3", config=configBoto, verify=certifi.where())
        else:
            self._bedrockRuntime = None
            self._bedrockAgentRuntime = None
            self._s3Client = None

    def _groq_chat(self, prompt: str, max_tokens=4000, temperature=1, top_p=0.99):
        if not self._groqApiKey:
            return {"status": "FAILED", "message": "GROQ API_KEY is not set (config GROQ.API_KEY or env GROQ_API_KEY)"}
        model = self._groqConfig.get("CHAT_MODEL", "llama-3.1-8b-instant")
        headers = {"Authorization": f"Bearer {self._groqApiKey}", "Content-Type": "application/json"}
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "top_p": float(top_p),
        }
        try:
            response = requests.post(GROQ_CHAT_URL, headers=headers, json=data, timeout=180)
            res = response.json()
            if response.status_code != 200:
                err = res.get("error", {})
                msg = err.get("message", response.text) if isinstance(err, dict) else str(res)
                return {"status": "FAILED", "message": f"Groq HTTP {response.status_code}: {msg}"}
            if "choices" in res and res["choices"]:
                return {"status": "SUCCESS", "message": str(res["choices"][0]["message"]["content"])}
            return {"status": "FAILED", "message": str(res)}
        except Exception as e:
            return {"status": "FAILED", "message": str(e)}

    def _groq_chat_with_image(self, image_bytes: bytes, prompt: str, max_tokens=500, temperature=1, top_p=0.999):
        if not self._groqApiKey:
            return {"status": "FAILED", "message": "GROQ API_KEY is not set"}
        model = self._groqConfig.get("VISION_MODEL", "llama-3.2-11b-vision-preview")
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        headers = {"Authorization": f"Bearer {self._groqApiKey}", "Content-Type": "application/json"}
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]
        data = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "top_p": float(top_p),
        }
        try:
            response = requests.post(GROQ_CHAT_URL, headers=headers, json=data, timeout=180)
            res = response.json()
            if response.status_code != 200:
                err = res.get("error", {})
                msg = err.get("message", response.text) if isinstance(err, dict) else str(res)
                return {"status": "FAILED", "message": f"Groq vision HTTP {response.status_code}: {msg}"}
            if "choices" in res and res["choices"]:
                return {"status": "SUCCESS", "message": str(res["choices"][0]["message"]["content"])}
            return {"status": "FAILED", "message": str(res)}
        except Exception as e:
            return {"status": "FAILED", "message": str(e)}

    def _openai_chat(self, prompt: str, max_tokens=4000, temperature=1, top_p=0.99):
        if not self._openaiApiKey:
            return {"status": "FAILED", "message": "OPENAI openai_api_key is not set (config OPENAI.openai_api_key or env OPENAI_API_KEY)"}
        model = self._openaiConfig.get("CHAT_MODEL", "gpt-4o-mini")
        headers = {"Authorization": f"Bearer {self._openaiApiKey}", "Content-Type": "application/json"}
        data = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "top_p": float(top_p),
        }
        try:
            response = requests.post(OPENAI_CHAT_URL, headers=headers, json=data, timeout=180)
            res = response.json()
            if response.status_code != 200:
                err = res.get("error", {})
                msg = err.get("message", response.text) if isinstance(err, dict) else str(res)
                return {"status": "FAILED", "message": f"OpenAI HTTP {response.status_code}: {msg}"}
            if "choices" in res and res["choices"]:
                return {"status": "SUCCESS", "message": str(res["choices"][0]["message"]["content"])}
            return {"status": "FAILED", "message": str(res)}
        except Exception as e:
            return {"status": "FAILED", "message": str(e)}

    def _openai_chat_with_image(self, image_bytes: bytes, prompt: str, max_tokens=500, temperature=1, top_p=0.999):
        if not self._openaiApiKey:
            return {"status": "FAILED", "message": "OPENAI openai_api_key is not set"}
        model = self._openaiConfig.get("VISION_MODEL", "gpt-4o-mini")
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        headers = {"Authorization": f"Bearer {self._openaiApiKey}", "Content-Type": "application/json"}
        content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]
        data = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": int(max_tokens),
            "temperature": float(temperature),
            "top_p": float(top_p),
        }
        try:
            response = requests.post(OPENAI_CHAT_URL, headers=headers, json=data, timeout=180)
            res = response.json()
            if response.status_code != 200:
                err = res.get("error", {})
                msg = err.get("message", response.text) if isinstance(err, dict) else str(res)
                return {"status": "FAILED", "message": f"OpenAI vision HTTP {response.status_code}: {msg}"}
            if "choices" in res and res["choices"]:
                return {"status": "SUCCESS", "message": str(res["choices"][0]["message"]["content"])}
            return {"status": "FAILED", "message": str(res)}
        except Exception as e:
            return {"status": "FAILED", "message": str(e)}

    '''
    This method is to invoke the LLM model and chat.

    parameters:
        prompt (str): Instruction/question to the model
        maxTokens (int): Maximum token length for output text.
        temperature (float): Sampling temperature.
        topP (float): Nucleus sampling parameter.

    returns:
        output (str): Output result by the LLM model.
    '''
    def Chat(self, prompt:str, maxTokens=4000, temperature=1, topP=0.99):
            if self._openaiConfig.get("USE_FOR_CHAT") and self._openaiApiKey:
                return self._openai_chat(prompt, max_tokens=maxTokens, temperature=temperature, top_p=topP)
            if self._groqConfig.get("USE_FOR_CHAT") and self._groqApiKey:
                return self._groq_chat(prompt, max_tokens=maxTokens, temperature=temperature, top_p=topP)

            # Gemini (disabled — use GROQ above)
            # if self._geminiConfig.get("USE_FOR_CHAT"):
            #     model = self._geminiConfig.get("CHAT_MODEL", "gemini-2.5-pro")
            #     return self.ChatWithGemini(...)

            if not self._bedrockRuntime:
                return {"status": "FAILED", "message": "No LLM backend: set OPENAI or GROQ in config, or configure AWS keys for Bedrock."}

            NativeRequest = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens":maxTokens,
                    "temperature": temperature,
                    "top_p": topP,
                    "messages": [
                        {
                            "role": "user",
                            "content": [{"type": "text", "text": prompt}],
                        }
                    ],
                }

            request = json.dumps(NativeRequest).encode("utf-8")
        
            try:
                response = self._bedrockRuntime.invoke_model(modelId=SONNET_35_MODEL_ID, body=request)
                modelOutput = json.loads(response["body"].read())
                answer = modelOutput["content"][0]["text"]
                if answer:
                    return {"status": "SUCCESS", "message": str(answer)}
                else:
                    return {"status": "FAILED", "message": "Failed to invoke the model"}
            except (BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError) as error:
                return {"status": "FAILED", "message": str(error)}


    '''
    This method is to invoke the LLM model and chat with streaming response.

    parameters:
        prompt (str): Instruction/question to the model
        llmModel (str): LLM model to be used.
        maxTokens (int): Maximum token length for output text.
        temperature (float): Sampling temperature.
        topP (float): Nucleus sampling parameter.

    returns:
        output (str): Output result by the LLM model.
    '''
    def StreamingChatResponse(self, prompt: str, maxTokens=200000, temperature=1, topP=0.99, modelId=SONNET_35_MODEL_ID):
        try:
            if not self._bedrockRuntime:
                return {"status": "failure", "message": "Bedrock not configured (optional when using GROQ for Chat)."}
            native_request = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": maxTokens,
                "temperature": temperature,
                "top_p": topP,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    }
                ],
            }
            body = json.dumps(native_request).encode("utf-8")

            response = self._bedrockRuntime.invoke_model_with_response_stream(
                modelId=modelId,
                body=body,
                contentType="application/json",
                accept="application/json"
            )

            stream = response.get("body")
            if not stream:
                return {"status": "failure", "message": "No streaming body returned."}

            llmMessage = ""

            for event in stream:
                chunk = event.get("chunk")
                if chunk and "bytes" in chunk:
                    textChunk = chunk["bytes"].decode("utf-8")
                    try:
                        parsed = json.loads(textChunk)
                        if parsed.get("type") == "content_block_delta":
                            delta = parsed.get("delta", {})
                            if delta.get("type") == "text_delta":
                                llmMessage += delta.get("text", "")
                    except json.JSONDecodeError:
                        continue

            return {"status": "success", "message": llmMessage}
        except (BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError) as error:
            return {"status": "failure", "message": str(error)}

    '''
    This method is to invoke the LLM model and chat with streaming response.

    parameters:
        prompt (str): Instruction/question to the model
        maxTokens (int): Maximum token length for output text.
        temperature (float): Sampling temperature.
        topP (float): Nucleus sampling parameter.

    returns:
        output (str): Output result by the LLM model.
    '''
    def TokenwiseStreamingChatResponse(self, prompt: str, maxTokens=1000000, temperature=1, topP=0.99, modelId=SONNET_35_MODEL_ID):
        try:
            if not self._bedrockRuntime:
                yield "[ERROR]: Bedrock not configured"
                return
            native_request = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": maxTokens,
                "temperature": temperature,
                "top_p": topP,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}],
                    }
                ],
            }
            body = json.dumps(native_request).encode("utf-8")

            response = self._bedrockRuntime.invoke_model_with_response_stream(
                modelId=modelId,
                body=body,
                contentType="application/json",
                accept="application/json"
            )

            stream = response.get("body")
            if not stream:
                yield "[ERROR]: No streaming body returned."
                return

            for event in stream:
                chunk = event.get("chunk")
                if chunk and "bytes" in chunk:
                    textChunk = chunk["bytes"].decode("utf-8")
                    try:
                        parsed = json.loads(textChunk)
                        if parsed.get("type") == "content_block_delta":
                            delta = parsed.get("delta", {})
                            if delta.get("type") == "text_delta":
                                token = delta.get("text", "")
                                if token:
                                    yield token  # 🔥 Yield token as it arrives
                    except json.JSONDecodeError:
                        continue

        except (BotoCoreError, ClientError, ConnectTimeoutError, ReadTimeoutError) as error:
            yield f"[ERROR]: {str(error)}"


    '''
    This method is to invoke the LLM model and chat with image.

    parameters:
      imageBytes: JPEG image file bytes(type = <class 'bytes'>).
      prompt (str): Instruction/question to the model
      maxTokens (int): Maximum token length for output text.
      temperature (float): Sampling temperature.
      topP (float): Nucleus sampling parameter.
            
    returns:
        output (str): Output result by the LLM model.
    '''
    def ChatWithImage(self, imageBytes=None, prompt:str=None, maxTokens=500, temperature=1, topP=0.999, modelId=SONNET_4_MODEL_ID):
        try:
            if self._openaiApiKey and (
                self._openaiConfig.get("USE_FOR_VISION") or self._openaiConfig.get("USE_FOR_CHAT")
            ):
                if imageBytes is None:
                    return {"status": "FAILED", "message": "No image uploaded"}
                use_prompt = (
                    prompt
                    if prompt
                    else "Decribe the file given to you and ask user for any specific question."
                )
                return self._openai_chat_with_image(
                    imageBytes, use_prompt, max_tokens=maxTokens, temperature=temperature, top_p=topP
                )
            if self._groqConfig.get("USE_FOR_VISION") and self._groqApiKey:
                if imageBytes is None:
                    return {"status": "FAILED", "message": "No image uploaded"}
                use_prompt = (
                    prompt
                    if prompt
                    else "Decribe the file given to you and ask user for any specific question."
                )
                return self._groq_chat_with_image(
                    imageBytes, use_prompt, max_tokens=maxTokens, temperature=temperature, top_p=topP
                )

            if not self._bedrockRuntime:
                return {
                    "status": "FAILED",
                    "message": "No vision LLM: set OPENAI or GROQ in config, or configure AWS Bedrock keys.",
                }

            if imageBytes and prompt:
                    encodedImage = base64.b64encode(imageBytes).decode("utf-8")
                    messages = {"role": "user",
                                    "content": [
                                        {
                                            "type": "image",
                                            "source": {
                                                "type": "base64",
                                                "media_type": "image/jpeg",
                                                "data": encodedImage,
                                            },
                                        },
                                        {"type": "text", "text": prompt},
                                    ],
                                }
            elif imageBytes is None:
                return {"status": "FAILED", "message": "No image uploaded"}
                
            elif imageBytes is not None and prompt is None:
                    encodedImage = base64.b64encode(imageBytes).decode("utf-8")
                    messages = {"role": "user",
                                    "content": [
                                        {
                                            "type": "image",
                                            "source": {
                                                "type": "base64",
                                                "media_type": "image/jpeg",
                                                "data": encodedImage,
                                            },
                                        },
                                        {"type": "text", "text": "Decribe the file given to you and ask user for any specific question."},
                                    ],
                                }
            
            nativeRequest = {
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": maxTokens,
                    "temperature": temperature,
                    "top_p": topP,
                    "messages": [messages]
                }

            request = json.dumps(nativeRequest)
            response = self._bedrockRuntime.invoke_model(modelId=modelId, body=request)
            modelOutput = json.loads(response["body"].read())
            answer = modelOutput["content"][0]["text"]
            if answer:
                return {"status": "SUCCESS", "message": str(answer)}
            else:
                return {"status": "FAILED", "message": "Failed to invoke the model"}
        except (BotoCoreError, ClientError) as error:
            return {"status": "FAILED", "message": str(error)}
        

    """
        This method streams output from the LLM model using an image and prompt.

        Parameters:
            imageBytes (bytes): JPEG image bytes.
            llmModel (str): LLM model to be used.
            prompt (str): Instruction or question to the model.
            maxTokens (int): Maximum number of tokens.
            temperature (float): Sampling temperature.
            topP (float): Top-p nucleus sampling.

        Yields:
            str: Streamed output tokens from the model.
        """
    def StreamingChatWithImage(self, imageBytes=None, llmModel=SONNET_35_MODEL_ID, prompt: str = None, maxTokens=500, temperature=1, topP=0.999):
    
        try:
            if imageBytes and prompt:
                encodedImage = base64.b64encode(imageBytes).decode("utf-8")
                messages = {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": encodedImage,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            elif imageBytes is None:
                yield "[ERROR] No image uploaded"
                return
            elif imageBytes is not None and prompt is None:
                encodedImage = base64.b64encode(imageBytes).decode("utf-8")
                messages = {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": encodedImage,
                            },
                        },
                        {
                            "type": "text",
                            "text": "Describe the file given to you and ask user for any specific question.",
                        },
                    ],
                }

            nativeRequest = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": maxTokens,
                "temperature": temperature,
                "top_p": topP,
                "messages": [messages],
            }

            request = json.dumps(nativeRequest).encode("utf-8")

            # Use the streaming method
            response = self._bedrockRuntime.invoke_model_with_response_stream(
                modelId=llmModel,
                body=request,
                contentType="application/json",
                accept="application/json"
            )

            stream = response.get("body")
            if not stream:
                yield "[ERROR] No streaming body returned"
                return

            for event in stream:
                chunk = event.get("chunk")
                if chunk and "bytes" in chunk:
                    try:
                        text_chunk = chunk["bytes"].decode("utf-8")
                        parsed = json.loads(text_chunk)

                        if parsed.get("type") == "content_block_delta":
                            delta = parsed.get("delta", {})
                            if delta.get("type") == "text_delta":
                                yield delta.get("text", "")
                    except json.JSONDecodeError:
                        continue

        except (BotoCoreError, ClientError) as error:
            yield f"[ERROR] {str(error)}"

    '''
    This method is to invoke the Gemini model (Google Generative Language API) for text and image+text prompts.
 
    parameters:
        prompt (str): Instruction/question to the model
        imageBytes (bytes, optional): Image file bytes (JPEG/PNG). If provided, sends image+text prompt.
        model (str): Gemini model name (e.g., 'gemini-2.5-pro')
        api_key (str, optional): Gemini API key. If not provided, uses default/hardcoded key.
 
    returns:
        output (dict): {"status": "SUCCESS"/"FAILED", "message": result}
    '''
    def ChatWithGemini(
        self,
        prompt,
        imageBytes=None,
        model="gemini-2.5-pro",
        api_key=None,
        max_output_tokens=None,
        temperature=None,
        top_p=None,
    ):
        """Gemini API disabled — use GROQ via Chat() / ChatWithImage()."""
        return {
            "status": "FAILED",
            "message": "Gemini is disabled. Use GROQ (default_config GROQ.* or env GROQ_API_KEY).",
        }


    '''
    This method is to invoke the LLM model and chat with documents.

    parameters:
      documentPath (str): S3 URI of document .
      prompt (str): Instruction/question to the model
      maxTokens (int): Maximum token length for output text.
      temperature (float): Sampling temperature.
      topP (float): Nucleus sampling parameter.
            
    returns:
        output (str): Output result by the LLM model.
    '''
    def ChatWithDocument(self, documentPath:str,  prompt:str, llmModel=SONNET_3_MODEL_ID, maxTokens=4000, temperature=1, topP=0.99):
        try:
            if not self._bedrockAgentRuntime:
                return {"status": "FAILED", "message": "Bedrock agent runtime not configured (AWS keys empty)."}
            response = self._bedrockAgentRuntime.retrieve_and_generate(
                input={'text': prompt},
                retrieveAndGenerateConfiguration={
                    'type': 'EXTERNAL_SOURCES',
                    'externalSourcesConfiguration': {
                        'modelArn': llmModel,
                        'sources': [
                            {
                                "sourceType": "S3",
                                "s3Location": {
                                    "uri": documentPath
                                }
                            }
                        ],
                        'generationConfiguration': {
                            'inferenceConfig': {
                                'textInferenceConfig': {
                                    'maxTokens': maxTokens,
                                    'temperature': temperature,
                                    'topP': topP

                                }
                            }
                        }
                    }
                }
            )
            time.sleep(2)
            answer = response["output"]["text"]
            if answer:
                return {"status": "SUCCESS", "message": answer}
            else:
                return {"status": "FAILED", "message": "Failed to retrieve the response"}
            
        except (BotoCoreError, ClientError) as error:
            return {"status": "FAILED", "message": str(error)}


    '''
    This method is used for Createing s3 bucket.

    Parameters:
        bucketName (str): Name of the S3 bucket.

    Returns:    
        output (str): Output result by the LLM model.
    '''
    def CreateBucket(self, bucketName):
        try:
            if not self._s3Client:
                return {"status": "FAILED", "message": "S3 client not configured (AWS keys empty)."}
            self._s3Client.create_bucket(Bucket=bucketName, CreateBucketConfiguration={'LocationConstraint': self._awsConfig["REGION"]})
            time.sleep(2)
            return {"status": "SUCCESS", "message": "Bucket created successfully"}
        except (BotoCoreError, ClientError) as error:
            return {"status": "FAILED", "message": str(error)}
        

    '''
    This method is used for uploading the file to S3 bucket.
    Parameters:
        bucketName (str): Name of the S3 bucket.
        file (str): Path of the file to be uploaded.
        remotePath (str): Path in the S3 bucket where the file will be uploaded.
    Returns:
        output (str): Output result by the LLM model.
    '''
    def UploadFile(self, bucketName, file, remotePath):
            try:
                if not self._s3Client:
                    return {"status": "FAILED", "message": "S3 client not configured."}
                self._s3Client.upload_file(Filename=file, Bucket=bucketName, Key=remotePath)
                return {"status": "SUCCESS", "message": f'File uploaded successfully'}
            except (BotoCoreError, ClientError) as e:
                return {"status": "FAILED", "message": str(e)}
            

    '''
    This method is to upload the file object to S3 bucket.
    parameters:
      bucketName (str): Name of the S3 bucket.
      fileObject (file): File object to be uploaded.
      objectName (str): Name of the object in S3 bucket.
    returns:
        output (str): Output result by the LLM model.'''
    def UploadObject(self, bucketName, fileObject, objectName):
        try:
            if not self._s3Client:
                return {"status": "FAILED", "message": "S3 client not configured."}
            transferConfig = boto3.s3.transfer.TransferConfig(use_threads=False)
            fileObject.seek(0)
            self._s3Client.upload_fileobj(fileObject, bucketName, objectName, Config=transferConfig)
            file_url = self._s3Client.generate_presigned_url('get_object',Params={'Bucket': bucketName, 'Key': objectName}, ExpiresIn=518400)
            return {"status": "SUCCESS", "message": f'File object uploaded successfully to "{bucketName}/{objectName}"', "file_url": file_url}
        except (BotoCoreError, ClientError) as error:
            return {"status": "FAILED", "message": str(error)}


    '''
    This method is used for deleting the object from s3 bucket.

    Parameters:
        bucketName (str): Name of the S3 bucket.
        fileName (str): Name of the file to be deleted.

    Returns:
        output (str): Output result by the LLM model.
    '''
    def DeleteFile(self, bucketName, fileName):
        try:
            if not self._s3Client:
                return {"status": "FAILED", "message": "S3 client not configured."}
            self._s3Client.delete_object(Bucket=bucketName, Key=fileName)
        except (BotoCoreError, ClientError) as error:
            return {"status": "FAILED", "message": str(error)}


    '''
    This method used for the checking whether the bucket exists or not.

    Parameters: 
        bucketName (str): Name of the S3 bucket.

    Returns:
        output (bool): True if bucket exists, False otherwise.
    '''
    def CheckBucket(self, bucketName):
        try:
            if not self._s3Client:
                return False
            self._s3Client.head_bucket(Bucket=bucketName)
            return True
        except (BotoCoreError, ClientError) as error:
            return False


    '''
    This method is used for langchain RAG tool for embedding the query and searchiing in knowledgebase.

    Parameters:
        embeddingModel (str): Name of the embedding model

    Returns:
        Output (str):Output by the embedding model
    '''
    def TextEmbeddings(self,embeddingModel):
        if not self._bedrockRuntime:
            raise RuntimeError("Bedrock not configured; TextEmbeddings requires AWS credentials.")
        return BedrockEmbeddings(model_id= embeddingModel, client=self._bedrockRuntime)


    '''
    This method is used for calling llm model in langchain agent.

    Parameters:
        llmModel (str): Name of the llm model

    Returns:
        Output (str):Output by the llm model
    '''
    def AgentLlmChat(self, modelId=SONNET_3_MODEL_ID, maxTokens=4000, temperature=0):
        if not self._bedrockRuntime:
            raise RuntimeError("Bedrock not configured; AgentLlmChat requires AWS credentials.")
        return ChatBedrock(
            model_id=modelId, 
            client=self._bedrockRuntime,
            model_kwargs={"max_tokens": maxTokens, "temperature": temperature}
        )