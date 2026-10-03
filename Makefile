TARGET = rixface
TYPE = ps-exe

SRCS = \
rixface.cpp \

ifeq ($(TEST),true)
CPPFLAGS = -Werror
endif
CXXFLAGS = -std=c++20

include third_party/nugget/psyqo/psyqo.mk
