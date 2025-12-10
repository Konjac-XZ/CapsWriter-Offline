'''

https://cv.j20.cc/

云剪切板

一个无依赖即用即走的剪切板，支持 web curl


*请输入5~1000个字符

*使用说明:

1. 提交文字或上传文件后得到一个唯一链接，例如 https://cv.j20.cc/b/xxxx

2. 命令行提交文字 curl https://cv.j20.cc/api/board -d "text=示例文字"

3. 命令行上传文件 curl https://cv.j20.cc/api/board -F "file=@some.file"

4. 使用时，对于文字 curl https://cv.j20.cc/b/xxxx 或者 浏览器打开链接

5. 使用时，对于文件 curl -O https://cv.j20.cc/b/xxxx 或者 浏览器打开链接下载

6. 历史记录为48h内最近20条，依靠cookie标记

'''



import requests
import json
from html.parser import HTMLParser

class MyHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.content = ""
        self.in_content = False

    def handle_starttag(self, tag, attrs):
        if tag == "p" and attrs == [('id', 'content')]:
            self.in_content = True

    def handle_endtag(self, tag):
        if tag == "p":
            self.in_content = False

    def handle_data(self, data):
        if self.in_content:
            self.content += data

class CloudClipboard:
    def __init__(self):
        self.url = 'https://cv.j20.cc/api/board'

    def post_data(self, text):
        data = {'text': text}
        response = requests.post(self.url, data=data)
        if response.status_code == 200:
            response_data = json.loads(response.text)
            if 'k' in response_data['data']:
                k_value = response_data['data']['k']
                url = f'https://cv.j20.cc/b/{k_value}'
                return url
            else:
                # print('Failed to post data.')
                return None
        else:
            print(json.loads(response.text))
            return None

    def get_data(self, url):
        response = requests.get(url)
        if response.status_code == 200:
            parser = MyHTMLParser()
            parser.feed(response.text)
            content = parser.content
            return content
        else:
            # print('Failed to get data.')
            return None

if __name__ == '__main__':
    text = 'Hello https://cv.j20.cc/'

    url = CloudClipboard().post_data(text)
    print(url)

    content = CloudClipboard().get_data(url)
    print(content)
