通义千问系列的录音文件识别模型能将录制好的音频精准地转换为文本，支持多语言识别、歌唱识别、噪声拒识等功能。

## **支持的模型**

提供正式版（Qwen3-ASR）和Beta版（Qwen-Audio-ASR）两种模型。正式版具备更强的功能和稳定性，Beta版仅供体验。

Qwen3-ASR

Qwen-Audio-ASR

基于通义千问多模态基座，支持多语言识别、歌唱识别、噪声拒识等功能，推荐用于生产环境。

具备如下优势：

- **多语种高精度识别：**支持多语言高精度语音识别（涵盖普通话及多种方言，如粤语、四川话等）。
- **复杂环境适应：**具备应对复杂声学环境的能力，支持自动语种检测与智能非人声过滤。
- **高精度歌唱识别：**即使在伴随背景音乐（BGM）的情况下，也能实现整首歌曲的准确转写。
- **上下文增强：**通过配置上下文提高识别准确率。参见[上下文增强](https://help.aliyun.com/zh/model-studio/qwen-speech-recognition?mode=pure#33fdf0438d5jd)。

| **模型名称**                                      | **版本** | **支持的语言**                                               | **支持的采样率** | **单价**     | **免费额度**[**（注）**](https://help.aliyun.com/zh/model-studio/new-free-quota#591f3dfedfyzj) |
| ------------------------------------------------- | -------- | ------------------------------------------------------------ | ---------------- | ------------ | ------------------------------------------------------------ |
| qwen3-asr-flash当前等同qwen3-asr-flash-2025-09-08 | 稳定版   | 中文（普通话、四川话、闽南语、吴语、粤语）、英语、日语、德语、韩语、俄语、法语、葡萄牙语、阿拉伯语、意大利语、西班牙语 | 16kHz            | 0.00022元/秒 | 36,000秒（10小时）有效期：百炼开通后90天内                   |
| qwen3-asr-flash-2025-09-08                        | 快照版   |                                                              |                  |              |                                                              |



## **功能特性**

|      |      |      |
| ---- | ---- | ---- |
|      |      |      |

| **功能特性**                                        | **Qwen3-ASR**                                                | **Qwen-Audio-ASR**        |
| --------------------------------------------------- | ------------------------------------------------------------ | ------------------------- |
| **接入方式**                                        | Java/Python SDK，HTTP API                                    | Java/Python SDK，HTTP API |
| **多语言**                                          | 中文（普通话、四川话、闽南语、吴语、粤语）、英文、日语、德语、韩语、俄语、法语、葡萄牙语、阿拉伯语、意大利语、西班牙语 | 中文、英文                |
| **上下文增强**                                      | ✅ 通过请求参数`text`配置Context实现定制化识别                | ❌                         |
| **语种识别**                                        | ✅ 将请求参数`enable_lid`设置为`true`可在识别结果中查看语种信息 | ❌                         |
| **指定待识别语种**                                  | ✅ 若已知音频的语种，可通过请求参数`language`指定待识别语种，以提升识别准确率 | ❌                         |
| **歌唱识别**                                        | ✅                                                            | ❌                         |
| **噪声拒识**                                        | ✅                                                            | ❌                         |
| **ITN（Inverse Text Normalization，逆文本规范化）** | ✅ 将请求参数`enable_itn`设置为`true`即可开启，该功能仅适用于中文和英文音频 | ❌                         |
| **标点符号预测**                                    | ✅                                                            | ❌                         |
| **流式输出**                                        | ✅                                                            | ✅                         |
| **音频输入方式**                                    | 本地音频：传入本地音频文件绝对路径在线音频：将音频文件上传至可公网访问的存储位置，并提供对应的URL（参见[Q：如何为API提供公网可访问的音频URL？](https://help.aliyun.com/zh/model-studio/qwen-speech-recognition?mode=pure#01d5f797c8ksz)） |                           |
| **待识别音频格式**                                  | aac、amr、avi、aiff、flac、flv、m4a、mkv、mp3、mp4、mpeg、ogg、opus、wav、webm、wma、wmv |                           |
| **待识别音频声道**                                  | 单声道                                                       |                           |
| **待识别音频采样率**                                | 16kHz                                                        |                           |
| **待识别音频大小**                                  | 音频文件大小不超过10MB，且时长不超过3分钟                    |                           |

## **快速开始**

暂不支持在线体验，如需使用请通过API接入。下面是调用API的示例代码。

开始前，请确保您已[获取API Key](https://help.aliyun.com/zh/model-studio/get-api-key)并[配置API Key到环境变量](https://help.aliyun.com/zh/model-studio/configure-api-key-through-environment-variables)。如果通过SDK调用，还需要[安装最新版DashScope SDK](https://help.aliyun.com/zh/model-studio/install-sdk)。

Qwen3-ASR

Qwen-Audio-ASR

> Qwen3-ASR模型为单轮调用模型，不支持多轮对话和自定义Prompt（包括System Prompt和User Prompt）。

录音文件URL

本地文件

流式输出

Python

Java

curl

 

```python
import os
import dashscope

messages = [
    {
        "role": "system",
        "content": [
            # 此处用于配置定制化识别的Context
            {"text": ""},
        ]
    },
    {
        "role": "user",
        "content": [
            {"audio": "https://dashscope.oss-cn-beijing.aliyuncs.com/audios/welcome.mp3"},
        ]
    }
]
response = dashscope.MultiModalConversation.call(
    # 若没有配置环境变量，请用百炼API Key将下行替换为：api_key = "sk-xxx"
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    model="qwen3-asr-flash",
    messages=messages,
    result_format="message",
    asr_options={
        # "language": "zh", # 可选，若已知音频的语种，可通过该参数指定待识别语种，以提升识别准确率
        "enable_lid":True,
        "enable_itn":False
    }
)
print(response)
```

完整结果以JSON格式输出到控制台。完整结果包含状态码、唯一的请求ID、识别后的内容以及本次调用的token信息。



## **核心用法：上下文增强**

**Qwen3-ASR**支持通过提供上下文（Context），对特定领域的专有词汇（如人名、地名、产品术语）进行识别优化，显著提升转写准确率。此功能远比传统的热词方案更灵活、强大。

**长度限制：**Context内容不超过 **10000** Token。

**用法：**调用API时，通过System Message的`text`参数传入文本即可。

**支持的文本类型：**包括（但不限于）

- 热词列表（多种分隔符格式，如热词 1、热词 2、热词 3、热词 4）
- 任意格式与长度的文本段落或篇章
- 混合内容：词表与段落的任意组合
- 无关或无意义文本（包括乱码，对无关文本的容错性极高，几乎不会受到负面影响）

**示例：**

某段音频正确识别结果应该为“投行圈内部的那些黑话，你了解哪些？首先，外资九大投行，Bulge Bracket，BB ...”。

| **不使用上下文增强**未使用上下文增强时，部分投行公司名称识别有误，例如 “Bird Rock” 正确应为 “Bulge Bracket”。识别结果：“投行圈内部的那些黑话，你了解哪些？首先，外资九大投行，Bird Rock，BB ...” | **使用上下文增强**使用上下文增强，对投行公司名称识别正确。识别结果：“投行圈内部的那些黑话，你了解哪些？首先，外资九大投行，Bulge Bracket，BB ...” |
| ------------------------------------------------------------ | ------------------------------------------------------------ |
|                                                              |                                                              |

实现上述效果，可在上下文中加入以下任一内容：

- 词表**：**

  - 词表1：

     

    ```plaintext
    Bulge Bracket、Boutique、Middle Market、国内券商
    ```

  - 词表2：

     

    ```plaintext
    Bulge Bracket Boutique Middle Market 国内券商
    ```

  - 词表3：

     

    ```plaintext
    ['Bulge Bracket', 'Boutique', 'Middle Market', '国内券商']
    ```

- 自然语言：

   

  ```plaintext
  投行分类大揭秘！
  最近有不少澳洲的小伙伴问我，到底什么是投行？今天就来给大家科普一下，对于留学生来说，投行主要可以分为四大类：Bulge Bracket、Boutique、Middle Market和国内券商。
  Bulge Bracket投行：这就是我们常说的九大投行，包括高盛、摩根士丹利等。这些大行在业务范围和规模上都相当庞大。
  Boutique投行：这些投行规模相对较小，但业务领域非常专注。比如Lazard、Evercore等，它们在特定领域有着深厚的专业知识和经验。
  Middle Market投行：这类投行主要服务于中型公司，提供并购、IPO等业务。虽然规模不如大行，但在特定市场上有很高的影响力。
  国内券商：随着中国市场的崛起，国内券商在国际市场上也扮演着越来越重要的角色。
  此外，还有一些Position和business的划分，大家可以参考相关的图表。希望这些信息能帮助大家更好地了解投行，为未来的职业生涯做好准备！
  ```

- 有干扰的自然语言：有些文本和识别内容无关，例如下面这个示例里的人名

   

  ```plaintext
  投行分类大揭秘！
  最近有不少澳洲的小伙伴问我，到底什么是投行？今天就来给大家科普一下，对于留学生来说，投行主要可以分为四大类：Bulge Bracket、Boutique、Middle Market和国内券商。
  Bulge Bracket投行：这就是我们常说的九大投行，包括高盛、摩根士丹利等。这些大行在业务范围和规模上都相当庞大。
  Boutique投行：这些投行规模相对较小，但业务领域非常专注。比如Lazard、Evercore等，它们在特定领域有着深厚的专业知识和经验。
  Middle Market投行：这类投行主要服务于中型公司，提供并购、IPO等业务。虽然规模不如大行，但在特定市场上有很高的影响力。
  国内券商：随着中国市场的崛起，国内券商在国际市场上也扮演着越来越重要的角色。
  此外，还有一些Position和business的划分，大家可以参考相关的图表。希望这些信息能帮助大家更好地了解投行，为未来的职业生涯做好准备！
  王皓轩 李梓涵 张景行 刘欣怡 陈俊杰 杨思远 赵雨桐 黄志强 周子墨 吴雅静 徐若曦 孙浩然 胡瑾瑜 朱晨曦 郭文博 何静姝 高宇航 林逸飞 
  郑晓燕 梁博文 罗佳琪 宋明哲 谢婉婷 唐子骞 韩梦瑶 冯毅然 曹沁雪 邓子睿 萧望舒 许嘉树 
  程一诺 袁芷若 彭浩宇 董思淼 范景玉 苏子衿 吕文轩 蒋诗涵 丁沐宸 
  魏书瑶 任天佑 姜亦辰 华清羽 沈星河 傅瑾瑜 姚星辰 钟灵毓 阎立诚 金若水 陶然亭 戚少商 薛芷兰 邹云帆 熊子昂 柏文峰 易千帆
  ```

## **API参考**

[语音识别-通义千问API参考](https://help.aliyun.com/zh/model-studio/use-qwen-by-calling-api#69cac67a477k2)

## **模型应用上架及备案**

参见[应用合规备案](https://help.aliyun.com/zh/model-studio/compliance-and-launch-filing-guide-for-ai-apps-powered-by-the-tongyi-model)。

## 常见问题

### **Q：如何为API提供公网可访问的音频URL？**

推荐使用[阿里云对象存储OSS](https://help.aliyun.com/zh/oss/simple-upload/#a632b50f190j8)，它提供了高可用、高可靠的存储服务，并且可以方便地生成公网访问URL。

**在公网环境下验证生成的 URL 可正常访问：**可在浏览器或通过 curl 命令访问该 URL，确保音频文件能够成功下载或播放（HTTP状态码为200）。

### **Q：如何检查音频格式是否符合要求？**

可以使用开源工具[ffprobe](https://ffmpeg.org/ffprobe.html)快速获取音频的详细信息：

 

```sh
# 查询音频的容器格式(format_name)、编码(codec_name)、采样率(sample_rate)、声道数(channels)
ffprobe -v error -show_entries format=format_name -show_entries stream=codec_name,sample_rate,channels -of default=noprint_wrappers=1 your_audio_file.mp3
```

### **Q：**如何处理音频以满足模型要求？

可以使用开源工具[FFmpeg](https://ffmpeg.en.lo4d.com/download)对音频进行裁剪或格式转换：

- **音频裁剪：从长音频中截取片段**

   

  ```bash
  # -i: 输入文件
  # -ss 00:01:30: 设置裁剪的起始时间 (从1分30秒开始)
  # -t 00:02:00: 设置裁剪的持续时长 (裁剪2分钟)
  # -c copy: 直接复制音频流，不重新编码，速度快
  # output_clip.wav: 输出文件
  ffmpeg -i long_audio.wav -ss 00:01:30 -t 00:02:00 -c copy output_clip.wav
  ```

- **格式转换**

  例如，将任意音频转换为16kHz、16-bit、单声道WAV文件

   

  ```bash
  # -i: 输入文件
  # -ac 1: 设置声道数为1 (单声道)
  # -ar 16000: 设置采样率为16000Hz (16kHz)
  # -sample_fmt s16: 设置采样格式为16-bit signed integer PCM
  # output.wav: 输出文件
  ffmpeg -i input.mp3 -ac 1 -ar 16000 -sample_fmt s16 output.wav
  ```