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
target_link_libraries(CapsWriterSpeechTip PRIVATE ole32 oleaut32 advapi32 uuid)

set_target_properties(CapsWriterSpeechTip PROPERTIES
    PREFIX ""
    OUTPUT_NAME "CapsWriterSpeechTip"
)

include(CTest)
if(BUILD_TESTING)
    add_executable(EditSessionQueueTest
        tests/edit_session_queue_test.cpp
    )
    target_compile_features(EditSessionQueueTest PRIVATE cxx_std_20)
    target_compile_definitions(EditSessionQueueTest PRIVATE
        UNICODE
        _UNICODE
        WIN32_LEAN_AND_MEAN
        NOMINMAX
    )
    target_compile_options(EditSessionQueueTest PRIVATE /W4 /permissive- /EHsc)
    target_include_directories(EditSessionQueueTest PRIVATE src)
    add_test(NAME EditSessionQueueTest COMMAND EditSessionQueueTest)

    add_executable(ContextSnapshotTest
        tests/context_snapshot_test.cpp
    )
    target_compile_features(ContextSnapshotTest PRIVATE cxx_std_20)
    target_compile_options(ContextSnapshotTest PRIVATE /W4 /permissive- /EHsc)
    target_include_directories(ContextSnapshotTest PRIVATE src)
    add_test(NAME ContextSnapshotTest COMMAND ContextSnapshotTest)

    add_executable(DisplayAttributesTest
        tests/display_attributes_test.cpp
    )
    target_compile_features(DisplayAttributesTest PRIVATE cxx_std_20)
    target_compile_definitions(DisplayAttributesTest PRIVATE
        UNICODE
        _UNICODE
        WIN32_LEAN_AND_MEAN
        NOMINMAX
    )
    target_compile_options(DisplayAttributesTest PRIVATE /W4 /permissive- /EHsc)
    target_include_directories(DisplayAttributesTest PRIVATE src)
    target_link_libraries(DisplayAttributesTest PRIVATE uuid)
    add_test(NAME DisplayAttributesTest COMMAND DisplayAttributesTest)

    add_executable(DisplayAttributeProviderTest
        tests/display_attribute_provider_test.cpp
    )
    target_compile_features(DisplayAttributeProviderTest PRIVATE cxx_std_20)
    target_compile_definitions(DisplayAttributeProviderTest PRIVATE
        UNICODE
        _UNICODE
        WIN32_LEAN_AND_MEAN
        NOMINMAX
    )
    target_compile_options(
        DisplayAttributeProviderTest PRIVATE /W4 /permissive- /EHsc
    )
    target_include_directories(DisplayAttributeProviderTest PRIVATE src)
    target_link_libraries(DisplayAttributeProviderTest PRIVATE ole32 oleaut32 uuid)
    add_test(
        NAME DisplayAttributeProviderTest
        COMMAND DisplayAttributeProviderTest $<TARGET_FILE:CapsWriterSpeechTip>
    )
endif()
