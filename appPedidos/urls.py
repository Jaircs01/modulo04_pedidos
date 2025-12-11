from django.urls import path, include
from rest_framework import routers
from .views import PedidoViewSet, recibir_pedido_modulo3

router = routers.DefaultRouter()
router.register(r'pedidos', PedidoViewSet, basename='pedido')

urlpatterns = [
    path('pedidos/desde-modulo3/', recibir_pedido_modulo3, name='recibir-pedido-modulo3'),
    path('', include(router.urls)),
]