cmake_minimum_required(VERSION 3.24)
project(CapsWriterSpeechTip LANGUAGES CXX)

add_library(CapsWriterSpeechTip SHARED
    CapsWriterSpeechTip.def
    src/speech_tip.cpp
)

target_compile_features(CapsWriterSpeechTip PRIVATE cxx_std_20)
target_compile_definitions(CapsWriterSpeechTip PRIVATE
    UNICODE
    _UNICODE
    WIN32_LEAN_AND_MEAN
    NOMINMAX
)
target_compile_options(CapsWriterSpeechTip PRIVATE /W4 /permissive- /EHsc)
target_link_libraries(CapsWriterSpeechTip PRIVATE ole32 advapi32 uuid)

set_target_properties(CapsWriterSpeechTip PROPERTIES
    PREFIX ""
    OUTPUT_NAME "CapsWriterSpeechTip"
)
