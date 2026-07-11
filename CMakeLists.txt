cmake_minimum_required(VERSION 3.16)
project(quick_cppkh LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_executable(quick_cppkh src/main.cpp)

if(NOT WIN32)
  find_package(Threads REQUIRED)
  target_link_libraries(quick_cppkh PRIVATE Threads::Threads)
endif()
