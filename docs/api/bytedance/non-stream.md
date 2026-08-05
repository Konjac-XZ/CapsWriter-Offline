本接口提供录音文件转文本能力。上传音频后可直接返回识别结果，无需调用接口查询。支持时长不超过 2 小时、大小不超过 100MB 的 WAV / MP3 / OGG OPUS 文件。

&nbsp;

<span data-label="purple">POST</span> https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash

&nbsp;


<span id="ULAS3Id9"></span>
### 请求头


**X\-Api\-Key ** `string` <span data-api-tag="require|1RbFw3">必选</span>

API Key 可以从 [控制台>API Key管理](https://console.volcengine.com/speech/new/setting/apikeys?projectName=default.) 获取

注意：


* 本接口同时支持[旧版控制台](https://console.volcengine.com/speech/service/10035)的鉴权方式，详见[旧版控制台鉴权参考](https://www.volcengine.com/docs/6561/2534847?lang=zh)



**X\-Api\-Resource\-Id ** `string` <span data-api-tag="require|W2vM70">必选</span>

请求的模型版本，可选值：`volc.bigasr.auc_turbo`



**X\-Api\-Request\-Id ** `string` <span data-api-tag="require|W2vM70">必选</span>

用于提交和查询任务的任务ID，推荐传入随机生成的UUID



**X\-Api\-Sequence ** `string` <span data-api-tag="require|W2vM70">必选</span>

发包序号，固定值: `-1`




<span id="OG7QrhRG"></span>
### 请求体


**audio ** `dict` <span data-api-tag="require|WnVo1B">必选</span>


**url ** `string` <span data-api-tag="require|1zS7c2">必选</span>

音频链接



**language ** `string`

指定识别语种。

当前支持识别以下语种


* 中文普通话：`zh-CN`

* 英语：`en-US`

* 日语：`ja-JP`

* 印尼语：`id-ID`

* 西班牙语：`es-MX`

* 葡萄牙语：`pt-BR`

* 德语：`de-DE`

* 法语：`fr-FR`

* 韩语：`ko-KR`

* 菲律宾语：`fil-PH`

* 马来语：`ms-MY`

* 泰语：`th-TH`

* 阿拉伯语：`ar-SA`

* 意大利语：`it-IT`

* 孟加拉语：`bn-BD`

* 希腊语：`el-GR`

* 荷兰语：`nl-NL`

* 俄语：`ru-RU`

* 土耳其语：`tr-TR`

* 越南语：`vi-VN`

* 波兰语：`pl-PL`

* 罗马尼亚语：`ro-R0`

* 尼泊尔语：`ne-NP`

* 乌克兰语：`uk-UA`

* 粤语：`yue-CN`


注意：

当 `language` 参数为空时，模型支持识别以下语种：中文、英文、上海话、闽南话、四川话、陕西话、粤语



**format ** `string` <span data-api-tag="require|7sHXWE">必选</span>

音频格式。

可选值：`raw` / `wav` / mp3 / ogg / pcm / spx / amr / aac / m4a



**codec** `string`

音频编码格式。默认raw（pcm）

可选值：`raw` / `opus`



**rate** `int`

音频采样率。默认值为 `16000`



**bits** `int`

音频采样点位数。默认支持16bits



**channel** `int`

音频声道数，默认值为 `1`

可选值：

`1`:mono

`2`:stereo




**request** `object`


**model_name** `string` <span data-api-tag="require|tXLKeG">必选</span>

模型名称。目前仅支持 `bigmodel`


&nbsp;


**enable_itn** `bool`

是否将语音识别结果转换为规范的书面格式，默认为`true`。

开启后，系统会把语音里的口语化数字、金额、日期等自动转成阿拉伯数字和符号形式，让文本更简洁、更易读。

效果示例:


* "一九七零年" → "1970 年"

* "一百二十三美元" → "$123"



**enable_punc** `bool`

是否启用标点，默认值为`false`。

开启后，识别结果会自动添加逗号、句号、问号等标点符号，提升文本可读性



**enable_ddc** `bool`

是否启用语义顺滑，默认 `false`。

开启后，系统会删除或修正识别结果中的停顿词、语气词、语义重复词等不流畅内容，让文本更连贯、更易读。



**enable_channel_split** `bool`

是否启用双声道识别，默认 `false`。

开启后，返回结果会用 `channel_id` 标记声道

`1` :左声道

`2` :右声道



**show_utterances** `bool`

是否输出分句、分词及语音停顿信息，默认 `false`。


&nbsp;


**enable_auto_lang** `bool`

是否自动识别语种，默认 `false`。开启后，系统会自动检测音频所属语种。

支持自动识别以下语种：


* 中文普通话 `zh-CN`

* 英语：`en-US`

* 日语：`ja-JP`

* 印尼语：`id-ID`

* 西班牙语：`es-MX`

* 葡萄牙语：`pt-BR`

* 德语：`de-DE`

* 法语：`fr-FR`

* 韩语：`ko-KR`

* 菲律宾语：`fil-PH`

* 马来语：`ms-MY`

* 泰语：`th-TH`

* 阿拉伯语 `ar-SA`

* 意大利语 `it-IT`

* 孟加拉语 `bn-BD`

* 希腊语 `el-GR`

* 荷兰语 `nl-NL`

* 俄语 `ru-RU`

* 土耳其语 `tr-TR`

* 越南语 `vi-VN`

* 波兰语 `pl-PL`

* 罗马尼亚语 `ro-RO`

* 尼泊尔语 `ne-NP`

* 乌克兰语 `uk-UA`

* 粤语 `yue-CN`



**enable_lid** `bool`

是否启用中英文及方言识别，默认 `false`。

支持识别以下语言：中文、英文、上海话、闽南话、四川话、陕西话、粤语

开启后，会在 `additions` 中返回语种/场景标签，取值如下：


* `singing_en`：英文唱歌

* `singing_mand`：普通话唱歌

* `singing_dia_cant`：粤语唱歌

* `speech_en`：英文说话

* `speech_mand`：普通话说话

* `speech_dia_nan`：闽南语

* `speech_dia_wuu`：吴语（含上海话）

* `speech_dia_cant`：粤语说话

* `speech_dia_xina`：西南官话（含四川话）

* `speech_dia_zgyu`：中原官话（含陕西话）

* `other_langs`：其它语种（其它语种人声）

* `others`：检测不出（非语义人声和非人声）

* 返回为空则代表无法判断（例如传入音频过短等）


&nbsp;


**vad_segment** `bool`

语义分句（VAD分句），默认为`false`

注意：当`enable_channel_split`设置为`true`时，建议同时使用语义分句



**end_window_size** `int`

语音活动检测 (VAD) 的静音判停阈值，单位 ms。当检测到的连续静音时长达到该值时，判定一句话结束并触发分句。

范围：`[300,5000]` 

推荐值：`[800,1000]`



**sensitive_words_filter** `string`

是否开启敏感词过滤功能。开启后，可对识别结果中的敏感词做屏蔽或替换处理。

示例

```Bash
"sensitive_words_filter":{\"system_reserved_filter\":true,\"filter_with_empty\":[\"敏感词\"],\"filter_with_signed\":[\"敏感词\"]}"
```



**system_reserved_filter ** `bool`

是否启用系统内置敏感词库。启用后，命中的系统敏感词会被替换为 `*`



**filter_with_empty ** `string`

需替换为空字符串的自定义敏感词列表



**filter_with_signed ** `string`

需替换为 `*` 的自定义敏感词列表




**enable_poi_fc** `bool`

开启 POI function call。能调用专业的地图领域推荐词服务辅助识别，提高识别准确率。

示例：

```SQL
"request": {
    "enable_poi_fc": true,
    "corpus": {
        "context": "{\"loc_info\":{\"city_name\":\"北京市\"}}"
    }
}
```




**enable_music_fc** `bool`

对于语音识别困难的词语，能调用专业的音领域推荐词服务辅助识别



**corpus** `object`

语境词典。可自定义配置热词、替换词，配置后可提高特定语境下的词语识别准确率


**boosting_table_name ** `string`

热词词表名称。配置热词可优化该类词语的识别效果

热词可在[控制台>自学习平台](https://console.volcengine.com/speech/new/hot-word?projectName=default)中设置



**boosting_table_id ** `string`

热词词表id。配置热词可优化该类词语的识别效果


* 热词可在[控制台>自学习平台](https://console.volcengine.com/speech/new/hot-word?projectName=default)中设置

* 若传入的`boosting_table_name`和`boosting_table_id`对应的热词词表不一致，则以`boosting_table_id`为准



**correct_table_name ** `string`

替换词词表名称。配置替换词，可将模型识别出的特定词汇替换为目标词汇

替换词可在[控制台>自学习平台](https://console.volcengine.com/speech/new/correct-word?projectName=default)中配置



**correct_table_id ** `string`

替换词词表名称。配置替换词，可将模型识别出的特定词汇替换为目标词汇


* 替换词可在[控制台>自学习平台](https://console.volcengine.com/speech/new/correct-word?projectName=default)中配置

* 若传入的`correct_table_name`和`correct_table_id`对应的热词词表不一致，则以`correct_table_id`为准



**regex_correct_table_name**`string`

正则替换词表名称。相较于替换词的精确匹配替换，正则替换词适合批量格式转换（如日期格式统一、符号标准化）、模糊模式匹配等复杂场景


* 正则替换词可在[控制台>自学习平台](https://console.volcengine.com/speech/new/correct-word?projectName=default)中配置



**regex_correct_table_id ** `string`

正则替换词表id。


* 正则替换词可在[控制台>自学习平台](https://console.volcengine.com/speech/new/correct-word?projectName=default)中配置



**context ** `string`

上下文功能。在识别前传入辅助信息，帮助模型更准确地识别。支持热词直传、传入对话历史、场景等信息辅助理解用法，可查询[热词与上下文](https://docs.volcengine.com/docs/6561/2604976?lang=zh)最佳实践了解更多信息

示例：

```Python
{
  "corpus": {
    "context": {
      "hotwords": [
        { "word": "豆包" },
        { "word": "火山引擎" },
        { "word": "奥迪A4L" }
      ],
      "context_type": "dialog_ctx",
      "context_data": [
        { "text": "最近一轮助手的回答" },
        { "text": "最近一轮用户的提问" },
        { "text": "更早一轮助手的回答" },
        { "text": "更早一轮用户的提问" }
      ]
    }
  }
}
```



**hotwords ** `string`

热词列表直传，用于提升指定词汇的识别准确率。可查询[热词与上下文](https://docs.volcengine.com/docs/6561/2604976?lang=zh)最佳实践了解更多信息


**word** `string`

热词内容




**context_type** `string`

上下文类型，目前仅支持`dialog_ctx`



**context_data** `object`

上下文数据列表，用于传入历史对话等语境信息，需要和`context_type`一起使用


**text ** `string`

历史对话文本，帮助模型理解语境，提升识别准确率



**image_url ** `string`

图片 URL，用于提供视觉上下文，辅助理解语音内容







**callback  ** `string`

回调地址。

示例：

```Python
"callback": "http://xxx"
```




**callback_data** `string`

回调信息。

```Python
"callback_data":"$Request-Id"
```





<span id="pF6mxalL"></span>
### 响应


**task_id ** `string`

任务 ID，可通过该 ID 调用识别结果查询接口获取识别结果



**X\-Tt\-Logid ** `string`

服务端返回的 logid，方便定位问题



**X\-Api\-Status\-Code ** `string`

提交任务后服务端返回的状态码



**X\-Api\-Message ** `string`

提交任务后服务端返回的信息，`OK` 表示成功，其他值表示失败



```
import json
import time
import uuid
import requests
import base64

# Helper function: Download file
def download_file(file_url):
    response = requests.get(file_url)
    if response.status_code == 200:
        return response.content  # Return file content (binary)
    else:
        raise Exception(f"Download failed, HTTP status code: {response.status_code}")

# Helper function: Convert local file to Base64
def file_to_base64(file_path):
    with open(file_path, 'rb') as file:
        file_data = file.read()  # Read file content
        base64_data = base64.b64encode(file_data).decode('utf-8')  # Base64 encode
    return base64_data

# recognize_task function
def recognize_task():
    recognize_url = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
    
        
    headers = {
        "X-Api-Key": "<your_api_key>",
        "X-Api-Resource-Id": "volc.bigasr.auc_turbo", 
        "X-Api-Request-Id": str(uuid.uuid4()),
        "X-Api-Sequence": "-1", 
    }

    request = {
        "audio": {
             "url": "https://hongli7-test.tos-cn-beijing.volces.com/G0137_S0039.wav"
        },
        "request": {
            "model_name": "bigmodel",
            # "enable_itn": True,
            # "enable_punc": True,
            # "enable_ddc": True,
            # "show_utterances": True,

        },
    }

    response = requests.post(recognize_url, json=request, headers=headers)
    if 'X-Api-Status-Code' in response.headers:
        print(f'recognize task response header X-Api-Status-Code: {response.headers["X-Api-Status-Code"]}')
        print(f'recognize task response header X-Api-Message: {response.headers["X-Api-Message"]}')
        print(time.asctime() + " recognize task response header X-Tt-Logid: {}".format(response.headers["X-Tt-Logid"]))
        print(f'recognize task response content is: {response.json()}\n')
    else:
        print(f'recognize task failed and the response headers are:: {response.headers}\n')
        exit(1)
    return response

# recognizeMode function
def recognizeMode():
    start_time = time.time()
    print(time.asctime() + " START!")
    recognize_response = recognize_task()
    code = recognize_response.headers['X-Api-Status-Code']
    logid = recognize_response.headers['X-Tt-Logid']
    if code == '20000000':  # task finished
        f = open("result.json", mode='w', encoding='utf-8')
        f.write(json.dumps(recognize_response.json(), indent=4, ensure_ascii=False))
        f.close()
        print(time.asctime() + " SUCCESS! \n")
        print(f"Execution time: {time.time() - start_time:.6f} seconds")
    elif code != '20000001' and code != '20000002':  # task failed
        print(time.asctime() + " FAILED! code: {}, logid: {}".format(code, logid))
        print("headers:")
        # print(query_response.content)

def main(): 
    # Just run the recognition directly since parameters are configured inside recognize_task
    recognizeMode()
 
if __name__ == '__main__': 
    main()

```





```
curl -X POST "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash" \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: <your_api_key>" \
  -H "X-Api-Resource-Id: volc.bigasr.auc_turbo" \
  -H "X-Api-Request-Id: $(uuidgen)" \
  -H "X-Api-Sequence: -1" \
  -d '{
    "audio": {
      "url": "<audio_file_url>"
    },
    "request": {
      "model_name": "bigmodel",
      "enable_itn": true,
      "enable_punc": true,
      "enable_ddc": true,
      "enable_speaker_info": false,
      "show_utterances": true
    }
  }'
```

```
{
  "headers": {
    "X-Api-Status-Code": "20000000",
    "X-Api-Message": "OK",
    "X-Tt-Logid": "20260728145300B323FC3A93347A7A5617"
  },
  "body": {
    "audio_info": {
      "duration": 6312
    },
    "result": {
      "additions": {
        "duration": "6312"
      },
      "text": "刚刚还在想你怎么还不来找我聊天，你就来了，真是心有灵犀啊。",
      "utterances": [
        {
          "additions": {
            "channel_id": "1",
            "speaker": "1"
          },
          "start_time": 480,
          "end_time": 5880,
          "text": "刚刚还在想你怎么还不来找我聊天，你就来了，真是心有灵犀啊。",
          "words": [
            {
              "confidence": 0,
              "start_time": 480,
              "end_time": 600,
              "text": "刚"
            },
            {
              "confidence": 0,
              "start_time": 680,
              "end_time": 800,
              "text": "刚"
            },
            {
              "confidence": 0,
              "start_time": 800,
              "end_time": 960,
              "text": "还"
            },
            {
              "confidence": 0,
              "start_time": 960,
              "end_time": 1120,
              "text": "在"
            },
            {
              "confidence": 0,
              "start_time": 1120,
              "end_time": 1440,
              "text": "想"
            }
          ]
        }
      ]
    }
  }
}
```

